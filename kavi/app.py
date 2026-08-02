"""KaviApp - the Textual application that drives the interactive agent.

This class is both the UI (a Textual ``App``) and the engine's :class:`AgentCallbacks`
implementation, so streamed text, tool activity, and permission prompts all flow straight
into terminal widgets.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll

from kavi import CREATOR
from kavi.agent.context import Conversation
from kavi.agent.engine import AgentCallbacks, AgentEngine, PermissionResult
from kavi.agent.prompts import build_system_prompt
from kavi.commands.registry import CommandRegistry, build_command_registry
from kavi.config.schema import KaviConfig
from kavi.cost.tracker import CostLimitExceeded, CostTracker, CostWarning
from kavi.memory.loader import load_memory
from kavi.messages import Message, ToolUseBlock, Usage
from kavi.permissions.engine import PermissionEngine
from kavi.providers.base import LLMProvider
from kavi.providers.registry import build_provider
from kavi.session.models import LoadedSession, SessionMeta
from kavi.session.store import SessionStore
from kavi.tools.base import ToolResult
from kavi.tools.registry import ToolRegistry, build_builtin_registry
from kavi.tools.task import TaskTool
from kavi.ui.screens.repl import ReplScreen
from kavi.ui.widgets.banner import WelcomeBanner
from kavi.ui.widgets.messages import (
    AssistantMessage,
    NoticeMessage,
    ResponseStats,
    ThinkingMessage,
    UserMessage,
)
from kavi.ui.widgets.permission import PermissionDialog
from kavi.ui.widgets.prompt import PromptArea
from kavi.ui.widgets.status_bar import ModeLine, StatusBar
from kavi.ui.widgets.suggestions import CommandSuggestions
from kavi.ui.widgets.todo_list import TodoList
from kavi.ui.widgets.tool_card import ToolCard
from kavi.ui.widgets.working import WorkingIndicator


class KaviApp(App, AgentCallbacks):
    CSS_PATH = "ui/kavi.tcss"
    TITLE = "Kavi Code"
    SUB_TITLE = f"by {CREATOR}"

    BINDINGS = [
        Binding("ctrl+c", "interrupt", "Cancel / Quit", priority=True),
        Binding("escape", "interrupt_task", "Interrupt"),
        Binding("ctrl+l", "clear", "Clear"),
    ]

    def __init__(
        self,
        config: KaviConfig,
        cwd: Path | None = None,
        resume: LoadedSession | None = None,
    ) -> None:
        super().__init__()
        self.config = config
        self.cwd = cwd or Path.cwd()
        self._resume = resume

        self.registry: ToolRegistry = build_builtin_registry()
        self.registry.register(TaskTool())
        self.command_registry: CommandRegistry = build_command_registry()
        self.cost = CostTracker(
            max_cost_usd=config.max_cost_usd,
            warn_cost_usd=config.warn_cost_usd,
        )
        self.session_store = SessionStore()
        self.mcp_manager = None  # set in on_mount if configured
        self.mcp_sources: dict[str, str] = {}  # server name -> origin label

        from kavi.agent.profiles import get_profile
        from kavi.config.loader import profiles_dir

        self.profile = get_profile(config.profile, profiles_dir())

        # Load extensions: skills, hooks, and custom slash commands.
        from kavi.extensions.hooks import HookRunner
        from kavi.extensions.skills import load_skills
        from kavi.extensions.usercommands import load_user_commands

        self.skills = load_skills(self.cwd)
        self.hooks = HookRunner.load(self.cwd)
        reserved = {c.name for c in self.command_registry.all()}
        for user_cmd in load_user_commands(self.cwd, reserved):
            self.command_registry.register(user_cmd)

        skills_meta = [(s.name, s.description) for s in self.skills.values()] or None
        memory = load_memory(self.cwd)
        system_prompt = build_system_prompt(
            self.cwd,
            memory=memory,
            suffix=config.system_prompt_suffix,
            profile_prompt=self.profile.prompt,
            skills=skills_meta,
            plan_mode=config.permissions.effective_mode() == "plan",
            provider=config.provider.value,
            model=config.resolved_model(),
        )
        self.conversation = Conversation(system_prompt=system_prompt)

        self.provider: LLMProvider | None = None
        self.engine: AgentEngine | None = None
        self._provider_error: str | None = None

        self.session_meta: SessionMeta | None = None
        self._persisted = 0

        # Active streaming widgets for the in-flight turn.
        self._active_assistant: AssistantMessage | None = None
        self._active_thinking: ThinkingMessage | None = None
        self._tool_cards: dict[str, ToolCard] = {}
        self._todo_panel: TodoList | None = None
        self._agent_busy = False
        # Prompts submitted while a task is running are queued and run in order.
        self._queue: list[str] = []
        # Per-response stats tracking.
        self._turn_start: float = 0.0
        self._turn_input_tokens: int = 0
        self._turn_output_tokens: int = 0

    # -- setup -------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield from ReplScreen().compose()

    async def on_mount(self) -> None:
        if self.config.theme and self.config.theme in self.available_themes:
            self.theme = self.config.theme

        status = self.query_one("#status", StatusBar)
        status.provider = self.config.provider.value
        status.model = self.config.resolved_model()
        status.cost = self.cost.status()
        status.mode = self.config.permissions.effective_mode()
        self.query_one("#mode-line", ModeLine).mode = self.config.permissions.effective_mode()

        self.query_one(CommandSuggestions).set_commands(
            [(c.name, c.description) for c in self.command_registry.all()]
        )

        self._build_engine()

        await self._connect_mcp()

        if self._resume is not None:
            self._load_resume(self._resume)
        else:
            self.session_meta = self.session_store.create(
                self.cwd, self.config.provider.value, self.config.resolved_model()
            )
            await self._add(
                WelcomeBanner(
                    provider=self.config.provider.value,
                    model=self.config.resolved_model(),
                    cwd=self.cwd,
                )
            )

        if self._needs_setup():
            await self._nudge_setup()
        self.query_one("#prompt", PromptArea).focus()

    def _needs_setup(self) -> bool:
        """True when there is no usable provider (missing key / not configured)."""
        from kavi.config.schema import Provider

        if self.config.provider == Provider.OLLAMA:
            return False
        return self.provider is None

    async def _nudge_setup(self) -> None:
        provider = self.config.provider.value
        await self._add(
            NoticeMessage(
                "No API key detected for the current provider "
                f"({provider}). Let's get you set up."
            )
        )
        if self._provider_error:
            await self._add(NoticeMessage(self._provider_error))
        await self._add(
            NoticeMessage(
                "Get started in 3 steps:\n"
                "  1) /provider   - pick a provider (↑/↓ to navigate, ↵ to select)\n"
                "  2) /apikey <provider> <key>   - add its API key\n"
                "  3) /model      - pick a model once the key is set"
            )
        )

    def _build_engine(self) -> None:
        try:
            self.provider = build_provider(self.config)
            self._provider_error = None
        except Exception as exc:  # noqa: BLE001
            self.provider = None
            self._provider_error = f"Provider not ready: {exc}"
            self.engine = None
            return
        self.engine = AgentEngine(
            config=self.config,
            provider=self.provider,
            registry=self.registry,
            conversation=self.conversation,
            permissions=PermissionEngine(self.config.permissions),
            cwd=self.cwd,
            callbacks=self,
            tool_names=self.profile.allowed_tools,
            hooks=self.hooks,
            skills=self.skills,
        )

    def rebuild_provider(self) -> None:
        self._build_engine()
        status = self.query_one("#status", StatusBar)
        status.provider = self.config.provider.value
        status.model = self.config.resolved_model()
        if self._provider_error:
            raise RuntimeError(self._provider_error)

    def reload_memory(self) -> None:
        memory = load_memory(self.cwd)
        skills_meta = [(s.name, s.description) for s in self.skills.values()] or None
        self.conversation.system_prompt = build_system_prompt(
            self.cwd,
            memory=memory,
            suffix=self.config.system_prompt_suffix,
            profile_prompt=self.profile.prompt,
            skills=skills_meta,
            plan_mode=self.config.permissions.effective_mode() == "plan",
            provider=self.config.provider.value,
            model=self.config.resolved_model(),
        )

    def set_profile(self, name: str) -> str:
        """Switch the active use-case profile; rebuild prompt + engine. Returns its name."""
        from kavi.agent.profiles import get_profile
        from kavi.config.loader import profiles_dir

        self.profile = get_profile(name, profiles_dir())
        self.config.profile = self.profile.name
        self.reload_memory()
        if self.engine is not None:
            self.engine.tool_names = self.profile.allowed_tools
        return self.profile.name

    def set_mode(self, mode: str) -> str:
        """Switch the permission mode for the session. Returns the applied mode."""
        self.config.permissions.mode = mode  # type: ignore[assignment]
        self.config.permissions.yolo = mode == "bypass"
        if self.engine is not None:
            self.engine.permissions.set_mode(mode)
        # Plan mode changes the system prompt (read-only directive); refresh it.
        self.reload_memory()
        try:
            self.query_one("#status", StatusBar).mode = mode
            self.query_one("#mode-line", ModeLine).mode = mode
        except Exception:  # noqa: BLE001 - status bar may not be mounted (tests)
            pass
        return mode

    async def list_models(self) -> tuple[list[str], str | None]:
        """Fetch the available model ids for the active provider."""
        if self.provider is None:
            return [], self._provider_error or "No provider configured."
        try:
            return await self.provider.list_models(), None
        except Exception as exc:  # noqa: BLE001
            return [], str(exc)

    async def prompt_api_key(self, provider_id: str) -> str | None:
        """Open a modal asking the user to paste an API key. Returns it or None."""
        from kavi.providers.presets import get_preset
        from kavi.ui.widgets.apikey import ApiKeyPromptScreen

        preset = get_preset(provider_id)
        label = preset.label if preset else provider_id
        docs = preset.docs if preset else ""
        env = preset.api_key_env if preset else None
        return await self.push_screen_wait(ApiKeyPromptScreen(label, docs, env))

    def apply_theme(self, theme: str) -> None:
        """Set the active Textual theme and persist the choice."""
        from kavi.config.loader import save_user_config

        self.theme = theme
        self.config.theme = theme
        save_user_config({"theme": theme})

    def set_provider_credential(
        self, provider_id: str, api_key: str, base_url: str | None = None
    ):
        """Store an API key for a provider, persist it, switch to it, and rebuild."""
        from kavi.config.loader import save_credential
        from kavi.config.schema import Provider, ProviderCredentials
        from kavi.providers.presets import get_preset

        preset = get_preset(provider_id)
        creds = self.config.credentials.get(provider_id, ProviderCredentials())
        creds.api_key = api_key
        if base_url:
            creds.base_url = base_url
        elif preset and preset.base_url and not creds.base_url:
            creds.base_url = preset.base_url
        self.config.credentials[provider_id] = creds
        self.config.provider = Provider(provider_id)
        if preset:
            self.config.model = preset.default_model

        path = save_credential(
            provider_id, api_key, base_url or (preset.base_url if preset else None)
        )
        self.rebuild_provider()
        # Refresh the system prompt so the agent knows its new provider + model.
        self.reload_memory()
        return path

    async def _connect_mcp(self) -> None:
        # Start with Kavi's own configured servers...
        servers = dict(self.config.mcp_servers)
        origins = {name: "kavi (config.toml)" for name in servers}

        # ...then fold in servers discovered from Claude Code / Codex (native wins).
        if self.config.import_external_mcp:
            from kavi.extensions.mcp_import import discover_external_mcp

            discovered, disc_origins = discover_external_mcp(self.cwd)
            for name, cfg in discovered.items():
                if name not in servers:
                    servers[name] = cfg
                    origins[name] = disc_origins[name]

        self.mcp_sources = origins
        if not servers:
            return
        from kavi.mcp.manager import McpManager

        self.mcp_manager = McpManager(servers, self.registry)
        await self.mcp_manager.connect_all()
        for notice in self.mcp_manager.notices:
            await self._add(NoticeMessage(notice))

    def _load_resume(self, loaded: LoadedSession) -> None:
        self.session_meta = loaded.meta
        self.conversation.messages = list(loaded.messages)
        self._persisted = len(loaded.messages)
        self.call_after_refresh(self._render_history, loaded.messages)

    async def resume_session(self, session_id: str) -> bool:
        """Load a saved session into the live app (swaps the conversation)."""
        if self._agent_busy:
            await self._add(NoticeMessage("Finish or interrupt the current task before resuming."))
            return False
        loaded = self.session_store.load(session_id)
        if loaded is None:
            return False
        self.session_meta = loaded.meta
        self.conversation.messages = list(loaded.messages)
        self._persisted = len(loaded.messages)
        chat = self.query_one("#chat", VerticalScroll)
        await chat.remove_children()
        await self._render_history(loaded.messages)
        return True

    async def _render_history(self, messages: list[Message]) -> None:
        for m in messages:
            if m.role == "user":
                text = m.text().strip()
                if text:
                    await self._add(UserMessage(text))
            else:
                if m.text().strip():
                    widget = AssistantMessage()
                    await self._add(widget)
                    widget.set_text(m.text())
        await self._add(NoticeMessage("--- resumed session ---"))

    # -- input handling ----------------------------------------------------------

    def on_text_area_changed(self, event: PromptArea.Changed) -> None:
        if event.text_area.id != "prompt":
            return
        # Only drive the slash-command menu for single-line, unsubmitted input.
        self.query_one(CommandSuggestions).update_for(event.text_area.text)

    async def on_prompt_area_submitted(self, event: PromptArea.Submitted) -> None:
        await self.submit_text(event.text)

    async def submit_text(self, text: str) -> None:
        """Handle one submitted prompt/command; queue it if a task is running."""
        text = text.strip()
        self.query_one(CommandSuggestions).hide()
        if not text:
            return
        await self._add(UserMessage(text))
        if self._agent_busy:
            # Don't interrupt the running task: queue this to run right after.
            self._queue.append(text)
            await self._add(
                NoticeMessage(f"Queued ({len(self._queue)} waiting) - runs after the current task.")
            )
            return
        self._dispatch(text)

    def _dispatch(self, text: str) -> None:
        """Route a message to the command registry or the agent (assumes not busy)."""
        if self.command_registry.is_command(text):
            self.run_worker(self._handle_command(text), exclusive=False)
        else:
            self._start_agent(text)

    def _drain_queue(self) -> None:
        """Start the next queued submission, if any (called when a task finishes)."""
        if self._agent_busy or not self._queue:
            return
        self._dispatch(self._queue.pop(0))

    async def _handle_command(self, text: str) -> None:
        try:
            out = await self.command_registry.dispatch(self, text)
            await self._apply_command_output(out)
        finally:
            self.call_later(self._drain_queue)

    async def _apply_command_output(self, out) -> None:  # noqa: ANN001
        if out.quit:
            self.exit()
            return
        if out.clear:
            await self._clear_conversation()
        if out.text:
            await self._add(NoticeMessage(out.text))
        if out.prompt:
            self._start_agent(out.prompt)
        if out.select is not None:
            await self._run_selection(out.select)
        # refresh status in case model/provider changed
        status = self.query_one("#status", StatusBar)
        status.provider = self.config.provider.value
        status.model = self.config.resolved_model()

    async def _run_selection(self, selection) -> None:  # noqa: ANN001
        from kavi.ui.widgets.selection import SelectionScreen

        choice = await self.push_screen_wait(
            SelectionScreen(
                selection.title, selection.options, filterable=selection.filterable
            )
        )
        if choice is None:
            await self._add(NoticeMessage("Cancelled."))
            return
        follow = await selection.on_select(self, choice)
        await self._apply_command_output(follow)

    def _start_agent(self, text: str) -> None:
        if self.engine is None:
            self.call_later(self._add, NoticeMessage(self._provider_error or "No provider."))
            return
        self.run_worker(self._run_agent(text), exclusive=True, group="agent")

    async def _run_agent(self, text: str) -> None:
        self._agent_busy = True
        self._reset_stream()
        self._set_state("working")
        self.query_one(WorkingIndicator).start()
        self._turn_start = time.monotonic()
        self._turn_input_tokens = 0
        self._turn_output_tokens = 0
        try:
            assert self.engine is not None
            await self.engine.run(text)
        except asyncio.CancelledError:
            await self._add(NoticeMessage("Interrupted."))
            raise
        except Exception as exc:  # noqa: BLE001
            await self._add(NoticeMessage(f"Error: {exc}"))
        finally:
            elapsed = time.monotonic() - self._turn_start
            if self._turn_input_tokens or self._turn_output_tokens:
                await self._add(
                    ResponseStats(elapsed, self._turn_input_tokens, self._turn_output_tokens)
                )
            self._reset_stream()
            self._persist_new_messages()
            self._agent_busy = False
            self._set_state("ready")
            self.query_one(WorkingIndicator).stop()
            self.query_one("#prompt", PromptArea).focus()
            # Run the next queued submission (if the turn wasn't cancelled).
            self.call_later(self._drain_queue)

    # -- AgentCallbacks ----------------------------------------------------------

    async def on_text_delta(self, text: str) -> None:
        if self._active_assistant is None:
            self._active_assistant = AssistantMessage()
            await self._add(self._active_assistant)
        self._active_assistant.append(text)
        self._scroll()

    async def on_thinking_delta(self, text: str) -> None:
        if self._active_thinking is None:
            self._active_thinking = ThinkingMessage()
            await self._add(self._active_thinking)
        self._active_thinking.append(text)
        self._scroll()

    async def on_assistant_message(self, message: Message) -> None:
        # A turn boundary: stop appending to the current widgets.
        self._active_assistant = None
        self._active_thinking = None

    async def on_tool_start(self, block: ToolUseBlock, render: str) -> None:
        # TodoWrite renders as a dedicated checklist (below), not a tool card.
        if block.name == "TodoWrite":
            return
        card = ToolCard(render)
        self._tool_cards[block.id] = card
        await self._add(card)

    async def on_tool_progress(self, block: ToolUseBlock, output: str) -> None:
        card = self._tool_cards.get(block.id)
        if card is not None:
            card.set_progress(output)
        self._scroll()

    async def on_tool_result(self, block: ToolUseBlock, result: ToolResult) -> None:
        if result.ui_payload and "todos" in result.ui_payload and not result.is_error:
            await self._render_todos(result.ui_payload["todos"])
            self._scroll()
            return
        card = self._tool_cards.get(block.id)
        if card is not None:
            card.set_result(result)
        self._scroll()

    async def _render_todos(self, todos: list[dict]) -> None:
        """Update the live to-do checklist in place (or create it the first time)."""
        if self._todo_panel is None or not self._todo_panel.is_mounted:
            self._todo_panel = TodoList(todos)
            await self._add(self._todo_panel)
        else:
            self._todo_panel.set_todos(todos)

    async def on_turn_usage(self, usage: Usage) -> None:
        self._turn_input_tokens += usage.input_tokens
        self._turn_output_tokens += usage.output_tokens
        try:
            warning = self.cost.record(self.config.resolved_model(), usage)
            if isinstance(warning, CostWarning):
                await self.on_notice(str(warning))
        except CostLimitExceeded as exc:
            self.query_one("#status", StatusBar).cost = self.cost.status()
            raise
        self.query_one("#status", StatusBar).cost = self.cost.status()

    async def on_notice(self, text: str) -> None:
        await self._add(NoticeMessage(text))

    async def request_permission(
        self, tool_name: str, subject: str, render: str
    ) -> PermissionResult:
        result = await self.push_screen_wait(PermissionDialog(tool_name, subject, render))
        if result in ("allow", "always", "deny"):
            return result  # type: ignore[return-value]
        return "deny"

    # -- actions -----------------------------------------------------------------

    def action_interrupt(self) -> None:
        if self._agent_busy:
            self._queue.clear()
            self.workers.cancel_group(self, "agent")
        else:
            self.exit()

    def action_interrupt_task(self) -> None:
        """Esc cancels a running task and clears the queue (but never quits)."""
        if self._agent_busy:
            self._queue.clear()
            self.workers.cancel_group(self, "agent")

    async def action_clear(self) -> None:
        await self._clear_conversation()

    # -- helpers -----------------------------------------------------------------

    async def _clear_conversation(self) -> None:
        self.conversation.messages.clear()
        self._persisted = 0
        chat = self.query_one("#chat", VerticalScroll)
        await chat.remove_children()
        await self._add(NoticeMessage("Conversation cleared."))

    async def _add(self, widget) -> None:  # noqa: ANN001
        chat = self.query_one("#chat", VerticalScroll)
        await chat.mount(widget)
        chat.scroll_end(animate=False)

    def _scroll(self) -> None:
        self.query_one("#chat", VerticalScroll).scroll_end(animate=False)

    def _reset_stream(self) -> None:
        self._active_assistant = None
        self._active_thinking = None
        self._tool_cards.clear()
        # A fresh task starts a fresh checklist (the previous one stays in the log).
        self._todo_panel = None

    def _set_state(self, state: str) -> None:
        self.query_one("#status", StatusBar).state = state
        try:
            self.query_one("#mode-line", ModeLine).state = state
        except Exception:  # noqa: BLE001 - mode line may not be mounted (tests)
            pass

    def _persist_new_messages(self) -> None:
        if self.session_meta is None:
            return
        new = self.conversation.messages[self._persisted :]
        for m in new:
            self.session_store.append(self.session_meta, m)
        self._persisted = len(self.conversation.messages)

    async def on_unmount(self) -> None:
        if self.mcp_manager is not None:
            try:
                await self.mcp_manager.aclose()
            except Exception:  # noqa: BLE001
                pass
