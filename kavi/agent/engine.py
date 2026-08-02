"""The agent engine - the streaming tool-call loop at the heart of Kavi.

The engine is UI-agnostic. It reports progress through an :class:`AgentCallbacks` object
and asks for permission decisions through the same interface, so the Textual UI, a headless
console runner, and sub-agents can all drive it.
"""

from __future__ import annotations

import asyncio
import itertools
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from kavi.agent.context import Conversation
from kavi.config.schema import KaviConfig
from kavi.messages import (
    ContentBlock,
    Message,
    MessageDone,
    TextDelta,
    ThinkingDelta,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
)
from kavi.permissions.engine import PermissionEngine
from kavi.providers.base import LLMProvider, ProviderError
from kavi.tools.base import ToolContext, ToolResult
from kavi.tools.registry import ToolRegistry
from kavi.log import get_logger
from kavi.cost.tracker import CostLimitExceeded

logger = get_logger(__name__)

if TYPE_CHECKING:
    from kavi.extensions.hooks import HookRunner

PermissionResult = Literal["allow", "deny", "always"]

# Stop reasons that indicate the model was cut off mid-output (hit output token
# limit) rather than finishing naturally.  When this happens the engine injects a
# continuation prompt automatically so the user doesn't have to type "continue".
_LENGTH_STOP_REASONS = {"length", "max_tokens"}
_MAX_AUTO_CONTINUES = 3

_RETRYABLE = (ConnectionError, TimeoutError, asyncio.TimeoutError)

# Substrings that mark a provider rejecting the *model's* generated tool call
# (a malformed function name or badly-typed arguments). These are usually
# transient - regenerating the turn typically produces a valid call - so we
# retry them instead of failing the whole turn.
_BAD_GENERATION_MARKERS = (
    "tool call validation failed",
    "failed to call a function",
    "did not match schema",
    "tool_use_failed",
    "was not in request.tools",
)


def _is_bad_generation_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in _BAD_GENERATION_MARKERS)


class AgentCallbacks:
    """Override these to observe and steer an engine run. All methods are async no-ops."""

    async def on_text_delta(self, text: str) -> None: ...
    async def on_thinking_delta(self, text: str) -> None: ...
    async def on_assistant_message(self, message: Message) -> None: ...
    async def on_tool_start(self, block: ToolUseBlock, render: str) -> None: ...
    async def on_tool_progress(self, block: ToolUseBlock, output: str) -> None: ...
    async def on_tool_result(self, block: ToolUseBlock, result: ToolResult) -> None: ...
    async def on_turn_usage(self, usage: Usage) -> None: ...
    async def on_notice(self, text: str) -> None: ...

    async def request_permission(
        self, tool_name: str, subject: str, render: str
    ) -> PermissionResult:
        """Default: deny anything that reaches an interactive prompt."""
        return "deny"


class AgentEngine:
    def __init__(
        self,
        *,
        config: KaviConfig,
        provider: LLMProvider,
        registry: ToolRegistry,
        conversation: Conversation,
        permissions: PermissionEngine,
        cwd: Path | None = None,
        callbacks: AgentCallbacks | None = None,
        tool_names: list[str] | None = None,
        hooks: HookRunner | None = None,
        skills: dict | None = None,
    ) -> None:
        self.config = config
        self.provider = provider
        self.registry = registry
        self.conversation = conversation
        self.permissions = permissions
        self.cwd = cwd or Path.cwd()
        self.callbacks = callbacks or AgentCallbacks()
        # Restrict which tools are exposed (used by sub-agents).
        self.tool_names = tool_names
        # Lifecycle hooks (PreToolUse/PostToolUse/...) and loaded skills.
        self.hooks = hooks
        self.skills = skills or {}
        # Remember the last clamp we announced so we don't spam the notice.
        self._last_clamp_notice: int | None = None
        # Content blocks (e.g. images from view_image) staged by tools during a
        # tool batch, flushed as one user message after the batch so they don't
        # break the assistant->tool-result ordering the APIs require.
        self._pending_user_content: list[ContentBlock] = []
        # Per-engine queue for background-task completion notifications.
        # BashTool writes into this queue when run_in_background=True; the
        # _loop drains it after every tool batch so the model is notified.
        self._background_queue: asyncio.Queue[str] = asyncio.Queue()

    # -- public API --------------------------------------------------------------

    async def run(self, user_input: str) -> str:
        """Run a full turn (possibly many tool-call iterations). Returns final text."""
        if self.hooks is not None and self.hooks.any_for("UserPromptSubmit"):
            await self.hooks.run("UserPromptSubmit", self.cwd, payload={"prompt": user_input})
        self.conversation.add_user_text(user_input)
        try:
            return await self._loop()
        finally:
            if self.hooks is not None and self.hooks.any_for("Stop"):
                await self.hooks.run("Stop", self.cwd)

    async def continue_run(self) -> str:
        """Continue the loop without adding a new user message (history already set)."""
        return await self._loop()

    # -- staged user content (images, etc.) --------------------------------------

    def _stage_user_content(self, block: ContentBlock) -> None:
        """Queue a content block to attach as a user message after the tool batch."""
        self._pending_user_content.append(block)

    def _flush_pending_user_content(self) -> None:
        """Attach any staged content blocks as one user message, then clear."""
        if not self._pending_user_content:
            return
        self.conversation.add_message(
            Message(role="user", content=list(self._pending_user_content))
        )
        self._pending_user_content.clear()

    def _flush_background_notifications(self) -> None:
        """Drain pending background-task notifications into the conversation.

        Each notification is a plain string (e.g. 'Background command "npm run
        dev" completed (exit 0). Output log: ~/.kavi/tasks/abc123.log').
        We inject them as user messages so the model sees them on the next
        iteration of the loop.
        """
        messages: list[str] = []
        while not self._background_queue.empty():
            try:
                messages.append(self._background_queue.get_nowait())
            except asyncio.QueueEmpty:  # noqa: PERF203
                break
        if messages:
            combined = "\n\n".join(messages)
            self.conversation.add_user_text(combined)

    # -- core loop ---------------------------------------------------------------

    async def _loop(self) -> str:
        final_text = ""
        auto_continues = 0
        # max_turns <= 0 means run until the model stops calling tools on its own.
        steps = itertools.count() if self.config.max_turns <= 0 else range(self.config.max_turns)
        hit_limit = True
        for _ in steps:
            # Compact aggressively — loop up to 3 times to bring the context
            # back under limits (a single compaction may not be enough if tool
            # results are very large).
            for _compact_pass in range(3):
                if not self.conversation.needs_compaction():
                    break
                compacted = await self.conversation.compact_with_summary(self._summarize)
                if compacted:
                    await self.callbacks.on_notice(
                        "Context compacted (earlier turns summarized) to fit the window."
                    )
                else:
                    break
            
            # Drain background-task completion notifications so the model sees them.
            self._flush_background_notifications()

            message, usage, stop_reason = await self._stream_once()
            self.conversation.add_message(message)
            await self.callbacks.on_assistant_message(message)
            
            try:
                await self.callbacks.on_turn_usage(usage)
            except CostLimitExceeded as exc:
                await self.callbacks.on_notice(str(exc))
                break

            tool_uses = message.tool_uses()
            if not tool_uses:
                # Check if the model was cut off mid-output (hit token limit)
                # rather than finishing naturally.  If so, automatically inject
                # a continuation prompt so the task keeps going.
                if (
                    stop_reason in _LENGTH_STOP_REASONS
                    and auto_continues < _MAX_AUTO_CONTINUES
                ):
                    auto_continues += 1
                    await self.callbacks.on_notice(
                        f"Output was truncated (hit token limit); auto-continuing "
                        f"({auto_continues}/{_MAX_AUTO_CONTINUES})..."
                    )
                    self.conversation.add_user_text(
                        "Your previous response was cut off because it hit the output "
                        "token limit. Continue exactly from where you left off."
                    )
                    continue

                final_text = message.text()
                hit_limit = False
                break

            # A successful tool-call turn resets the auto-continue counter
            # because the model is making forward progress.
            auto_continues = 0
            results = await self._run_tools(tool_uses)
            self.conversation.add_tool_results(results)
            self._flush_pending_user_content()
            # Drain background-task completion notifications and inject each
            # one as a user message so the model learns the outcome.
            self._flush_background_notifications()
        if hit_limit and self.config.max_turns > 0:
            await self.callbacks.on_notice(
                f"Reached the maximum of {self.config.max_turns} steps; stopping. "
                "Send another message to continue, or raise `max_turns` in your config "
                "(0 = unlimited)."
            )
        return final_text

    async def _stream_once(self, max_retries: int = 3) -> tuple[Message, Usage, str | None]:
        attempt = 0
        while True:
            try:
                return await self._stream_attempt()
            except asyncio.CancelledError:
                raise
            except _RETRYABLE as exc:
                attempt += 1
                if attempt > max_retries:
                    logger.error("Provider request failed after retries: %s", exc)
                    raise ProviderError(f"Provider request failed after retries: {exc}") from exc
                delay = min(2**attempt, 10)
                logger.warning("Transient error (%s); retrying in %ds (%d/%d)", exc, delay, attempt, max_retries)
                await self.callbacks.on_notice(
                    f"Transient error ({exc}); retrying in {delay}s ({attempt}/{max_retries})."
                )
                await asyncio.sleep(delay)
            except Exception as exc:  # noqa: BLE001
                if not _is_bad_generation_error(exc):
                    logger.error("Provider request failed permanently: %s", exc)
                    raise
                attempt += 1
                if attempt > max_retries:
                    raise ProviderError(
                        "The model kept producing an invalid tool call that the provider "
                        f"rejected (after {max_retries} retries). Try rephrasing your request, "
                        "or switch to a model better at tool use with /model. "
                        f"(details: {exc})"
                    ) from exc
                await self.callbacks.on_notice(
                    f"The model sent a malformed tool call; regenerating "
                    f"({attempt}/{max_retries})."
                )
                await asyncio.sleep(min(attempt, 3))

    def _active_tool_names(self) -> list[str] | None:
        """Tool names exposed to the model, refined by the permission mode.

        In plan mode, hide every mutating tool so the model doesn't waste turns
        calling tools that would only be denied.
        """
        names = self.tool_names
        if self.permissions.config.effective_mode() != "plan":
            return names
        candidates = names if names is not None else self.registry.names()
        return [
            n
            for n in candidates
            if (tool := self.registry.get(n)) is not None and tool.is_read_only
        ]

    async def _stream_attempt(self) -> tuple[Message, Usage, str | None]:
        model = self.config.resolved_model()
        schemas = self.registry.schemas(self._active_tool_names())
        max_tokens = await self._effective_max_tokens(model, schemas)
        message: Message | None = None
        usage = Usage()
        stop_reason: str | None = None
        async for event in self.provider.stream(
            system=self.conversation.system_prompt,
            messages=self.conversation.messages,
            tools=schemas,
            model=model,
            max_tokens=max_tokens,
            temperature=self.config.temperature,
            thinking=self.config.thinking,
            thinking_budget_tokens=self.config.thinking_budget_tokens,
        ):
            if isinstance(event, TextDelta):
                await self.callbacks.on_text_delta(event.text)
            elif isinstance(event, ThinkingDelta):
                await self.callbacks.on_thinking_delta(event.text)
            elif isinstance(event, MessageDone):
                message = event.message
                usage = event.usage
                stop_reason = event.stop_reason
        if message is None:  # pragma: no cover - provider contract violation
            raise ProviderError("Provider stream ended without a final message.")
        return message, usage, stop_reason

    async def _effective_max_tokens(self, model: str, schemas) -> int:  # noqa: ANN001
        """Clamp the configured max_tokens to the model's output / rate limits."""
        from kavi.providers.limits import clamp_max_tokens, estimate_input_tokens

        requested = self.config.max_tokens
        provider_id = self.config.provider.value
        input_tokens = estimate_input_tokens(
            self.conversation.system_prompt, self.conversation.messages, schemas
        )
        effective = clamp_max_tokens(provider_id, model, requested, input_tokens)
        if effective < requested and self._last_clamp_notice != effective:
            self._last_clamp_notice = effective
            await self.callbacks.on_notice(
                f"Capped max output to {effective} tokens to fit {model}'s limits "
                f"(input ~{input_tokens} tokens). Use /clear to free up room."
            )
        return effective

    # -- tool execution ----------------------------------------------------------

    async def _run_tools(self, tool_uses: list[ToolUseBlock]) -> list[ToolResultBlock]:
        # Run concurrency-safe read-only tools in parallel; everything else sequentially,
        # preserving the original order in the returned results. Hooks must observe
        # calls one at a time, in order, so we disable parallelism when any are set.
        results: dict[str, ToolResultBlock] = {}
        parallel: list[ToolUseBlock] = []
        hooks_active = self.hooks is not None and (
            self.hooks.any_for("PreToolUse") or self.hooks.any_for("PostToolUse")
        )
        if not hooks_active:
            for block in tool_uses:
                tool = self.registry.get(block.name)
                if tool is not None and tool.is_read_only and tool.is_concurrency_safe:
                    parallel.append(block)

        if len(parallel) > 1:
            gathered = await asyncio.gather(*(self._invoke(b) for b in parallel))
            for block, res in zip(parallel, gathered, strict=True):
                results[block.id] = res

        for block in tool_uses:
            if block.id not in results:
                results[block.id] = await self._invoke(block)

        return [results[b.id] for b in tool_uses]

    async def _invoke(self, block: ToolUseBlock) -> ToolResultBlock:
        tool = self.registry.get(block.name)
        if tool is None:
            result = ToolResult.error(f"Unknown tool: {block.name}")
            await self.callbacks.on_tool_start(block, f"{block.name} (unknown)")
            await self.callbacks.on_tool_result(block, result)
            return self._to_result_block(block, result)

        try:
            data = tool.validate(block.input)
        except Exception as exc:  # noqa: BLE001 - surface schema errors to the model
            result = ToolResult.error(f"Invalid input for {block.name}: {exc}")
            await self.callbacks.on_tool_start(block, tool.name)
            await self.callbacks.on_tool_result(block, result)
            return self._to_result_block(block, result)

        render = tool.render_call(data)
        await self.callbacks.on_tool_start(block, render)

        subject = tool.permission_subject(data)
        decision = self.permissions.decide(block.name, subject, tool.default_permission())

        # Classify this specific call so the mode overlay and the destructive-call
        # guard can reason about it. A tool returns "ask" from classify_permission
        # when the call is high-risk (e.g. Bash detecting `rm -rf`).
        is_destructive = tool.classify_permission(data) == "ask"

        # Apply the session-wide mode (default / auto / plan / bypass). This can
        # auto-accept edits (auto), deny mutating tools (plan), or leave the
        # rule-based decision untouched (default). Never overrides an explicit deny.
        decision = self.permissions.apply_mode(
            decision,
            is_read_only=tool.is_read_only,
            is_destructive=is_destructive,
        )

        # Even when a broad allow rule / auto mode would permit it, force a prompt
        # for a destructive call unless we are in bypass mode. Plan mode has
        # already denied it above.
        if (
            decision == "allow"
            and is_destructive
            and self.permissions.config.effective_mode() != "bypass"
        ):
            decision = "ask"

        if decision == "ask":
            choice = await self.callbacks.request_permission(block.name, subject, render)
            if choice == "always":
                self.permissions.grant_session(block.name, subject or None)
                decision = "allow"
            else:
                decision = choice  # "allow" or "deny"

        if decision == "deny":
            result = ToolResult.error(self._deny_reason())
            await self.callbacks.on_tool_result(block, result)
            return self._to_result_block(block, result)

        # PreToolUse hooks may block the call (non-zero exit => deny).
        if self.hooks is not None and self.hooks.any_for("PreToolUse"):
            hook = await self.hooks.run(
                "PreToolUse", self.cwd, block.name, {"input": block.input}
            )
            if hook.blocked:
                result = ToolResult.error(
                    f"Blocked by a PreToolUse hook: {hook.message}. Do not retry; "
                    "adjust your approach."
                )
                await self.callbacks.on_tool_result(block, result)
                return self._to_result_block(block, result)

        ctx = ToolContext(
            cwd=self.cwd,
            config=self.config,
            run_subagent=self._run_subagent,
            complete=self._aux_complete,
            stage_user_content=self._stage_user_content,
            on_progress=lambda text: self.callbacks.on_tool_progress(block, text),
            extras={
                "skills": self.skills,
                "notification_queue": self._background_queue,
            },
        )
        try:
            result = await tool.run(data, ctx)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - tools must not crash the loop
            result = ToolResult.error(f"{block.name} raised an error: {exc}")

        # PostToolUse hooks observe the call (they cannot block).
        if self.hooks is not None and self.hooks.any_for("PostToolUse"):
            await self.hooks.run("PostToolUse", self.cwd, block.name, {"input": block.input})

        await self.callbacks.on_tool_result(block, result)
        return self._to_result_block(block, result)

    def _deny_reason(self) -> str:
        """Human/model-readable reason a call was denied, tailored to the mode."""
        if self.permissions.config.effective_mode() == "plan":
            return (
                "Denied: plan mode is read-only. Do not attempt edits or commands; "
                "produce a plan for the user to approve before switching modes."
            )
        return "Permission denied by the user."

    @staticmethod
    def _to_result_block(block: ToolUseBlock, result: ToolResult) -> ToolResultBlock:
        return ToolResultBlock(
            tool_use_id=block.id, content=result.content, is_error=result.is_error
        )

    # -- auxiliary LLM helper (wired into ToolContext.complete) ------------------

    _SUMMARIZE_SYSTEM = (
        "You compress a coding agent's conversation transcript into a dense hand-off "
        "summary. Preserve, as concise bullet points: the user's goal(s), key decisions "
        "and their rationale, files created or edited (with what changed), important "
        "command output or findings, and any unfinished work or open questions. Omit "
        "chit-chat. Do not invent anything. Output only the summary."
    )

    async def _summarize(self, transcript: str) -> str:
        """Summarize a transcript slice with the small/fast model (for compaction)."""
        return await self._aux_complete(
            self._SUMMARIZE_SYSTEM,
            f"TRANSCRIPT TO SUMMARIZE:\n\n{transcript}",
        )

    async def _aux_complete(self, system: str, user: str) -> str:
        """Cheap, tool-free completion for tools (e.g. WebFetch distillation).

        Uses the configured small/fast model. Returns "" on any failure so a
        tool can gracefully fall back instead of crashing the turn.
        """
        from kavi.messages import Message, TextBlock

        try:
            return await self.provider.complete(
                system=system,
                messages=[Message(role="user", content=[TextBlock(text=user)])],
                model=self.config.resolved_small_fast_model(),
            )
        except Exception:  # noqa: BLE001
            return ""

    # -- sub-agent hook (wired by the Task tool) ---------------------------------

    async def _run_subagent(
        self,
        prompt: str,
        tool_names: list[str],
        system_suffix: str | None = None,
        agent_name: str | None = None,
        team_context: str | None = None,
    ) -> str:
        """Spawn a nested engine with a restricted toolset. Imported lazily to avoid cycles."""
        from kavi.subagents.runner import run_subagent

        return await run_subagent(
            parent=self,
            prompt=prompt,
            tool_names=tool_names,
            system_suffix=system_suffix,
            agent_name=agent_name,
            team_context=team_context,
        )


def build_engine(
    *,
    config: KaviConfig,
    conversation: Conversation,
    registry: ToolRegistry,
    provider: LLMProvider,
    cwd: Path,
    callbacks: AgentCallbacks | None = None,
) -> AgentEngine:
    return AgentEngine(
        config=config,
        provider=provider,
        registry=registry,
        conversation=conversation,
        permissions=PermissionEngine(config.permissions),
        cwd=cwd,
        callbacks=callbacks,
    )
