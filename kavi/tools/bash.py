"""Bash tool - run a shell command in the working directory."""

from __future__ import annotations

import asyncio
import platform
import time

from pydantic import BaseModel, Field

from kavi.config.schema import PermissionDecision
from kavi.log import get_logger
from kavi.permissions.bashspec import classify
from kavi.tools.base import Tool, ToolContext, ToolResult
from kavi.tools.processkill import kill_process_tree

logger = get_logger(__name__)

MAX_OUTPUT_CHARS = 30_000
DEFAULT_TIMEOUT = 120
PROGRESS_INTERVAL = 1.0  # seconds between progress callbacks

# Commands that are likely long-running servers/watchers and benefit from a
# longer default timeout when *not* run in the background.
_SERVER_PREFIXES = (
    "npm run dev",
    "npm run start",
    "npm start",
    "yarn dev",
    "yarn start",
    "pnpm dev",
    "pnpm start",
    "vite",
    "webpack serve",
    "uvicorn",
    "gunicorn",
    "flask run",
    "python manage.py runserver",
    "python -m http.server",
    "cargo watch",
    "air",
    "nodemon",
    "next dev",
    "gatsby develop",
    "rails server",
    "rails s",
    "php artisan serve",
    "dotnet run",
    "go run",
)


def _is_server_command(command: str) -> bool:
    cmd = command.strip().lstrip("./")
    return any(cmd.startswith(p) for p in _SERVER_PREFIXES)


class BashInput(BaseModel):
    command: str = Field(description="The shell command to execute.")
    description: str | None = Field(
        default=None, description="A short description of what the command does."
    )
    timeout: int = Field(default=DEFAULT_TIMEOUT, description="Timeout in seconds.")
    run_in_background: bool = Field(
        default=False,
        description=(
            "Run this command in the background without waiting for it to finish. "
            "Use this for long-running processes such as dev servers, watchers, or "
            "any command that starts a service (e.g. 'npm run dev', 'uvicorn app:app', "
            "'python manage.py runserver', 'python -m http.server'). "
            "The command starts immediately and you receive a task ID and log file path. "
            "You will be notified when the process exits. "
            "Do NOT append '&' to the command when using this parameter."
        ),
    )


import os
import re

_ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def strip_ansi(text: str) -> str:
    """Remove raw terminal ANSI escape sequences from process output."""
    return _ANSI_RE.sub("", text)


def get_noninteractive_env() -> dict[str, str]:
    """Provide non-interactive environment variables so npm/npx/pip auto-confirm prompts."""
    env = os.environ.copy()
    env["CI"] = "true"
    env["npm_config_yes"] = "true"
    env["DEBIAN_FRONTEND"] = "noninteractive"
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PIP_NO_INPUT"] = "1"
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    # Werkzeug 2.3+ raises ValueError when the debugger is enabled in a
    # non-TTY subprocess (stdin=DEVNULL). Tell it we are the reloader child
    # so it skips the interactive-terminal check, and disable the debug PIN
    # prompt which cannot work headlessly.
    env.setdefault("WERKZEUG_RUN_MAIN", "true")
    env.setdefault("WERKZEUG_DEBUG_PIN", "off")
    return env


def _truncate(text: str) -> str:
    cleaned = strip_ansi(text)
    if len(cleaned) <= MAX_OUTPUT_CHARS:
        return cleaned
    head = cleaned[: MAX_OUTPUT_CHARS // 2]
    tail = cleaned[-MAX_OUTPUT_CHARS // 2 :]
    return f"{head}\n\n... (output truncated) ...\n\n{tail}"


class BashTool(Tool):
    name = "Bash"
    description = """
    Execute a shell command and return its combined stdout/stderr and exit code. Runs in
    the current working directory. Do not use this for reading or editing files (use Read /
    Edit / Write) or for searching (use Grep / Glob). Quote paths that contain spaces.

    For long-running servers or watchers (npm run dev, uvicorn, flask run, etc.) set
    run_in_background=true so the command starts immediately without blocking.
    """
    InputModel = BashInput

    def permission_subject(self, data: BashInput) -> str:  # type: ignore[override]
        return data.command

    def default_permission(self) -> PermissionDecision:
        """Read-only inspection commands auto-allow; anything else asks."""
        return "ask"

    def classify_permission(self, data: BashInput) -> PermissionDecision | None:  # type: ignore[override]
        """Force a prompt for destructive commands; auto-allow pure reads.

        Returning ``"ask"`` makes a destructive command (``rm -rf``, ``git push
        --force``, ...) prompt even under a broad ``Bash`` allow rule. Read-only
        commands return ``None`` so the normal rules apply (they still respect an
        explicit deny).
        """
        classification = classify(data.command)
        if classification.destructive:
            return "ask"
        return None

    def render_call(self, data: BashInput) -> str:  # type: ignore[override]
        suffix = " [background]" if data.run_in_background else ""
        return f"$ {data.command}{suffix}"

    async def run(self, data: BashInput, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        if data.run_in_background:
            return await self._run_background(data, ctx)
        return await self._run_foreground(data, ctx)

    # ------------------------------------------------------------------
    # Background execution
    # ------------------------------------------------------------------

    async def _run_background(self, data: BashInput, ctx: ToolContext) -> ToolResult:
        """Spawn the command detached; return immediately with task metadata."""
        from kavi.tools.background import spawn_background_task

        # Retrieve the per-engine notification queue from ToolContext extras.
        notification_queue = (ctx.extras or {}).get("notification_queue")
        if notification_queue is None:
            logger.warning("background execution requested but no notification_queue in context")
            return ToolResult(
                content=(
                    "Warning: background execution is not available in this context "
                    "(no notification_queue). Running in foreground instead.\n\n"
                ),
                is_error=False,
                title=f"$ {data.command}",
            )

        description = data.description or data.command
        try:
            task_id, log_path = await spawn_background_task(
                command=data.command,
                description=description,
                cwd=ctx.cwd,
                notification_queue=notification_queue,
            )
        except RuntimeError as exc:
            return ToolResult.error(str(exc), title=f"$ {data.command}")

        content = (
            f"Background task started.\n"
            f"Task ID : {task_id}\n"
            f"Log file: {log_path}\n\n"
            f"You will be notified when the process exits. "
            f"Use the Read tool on the log file to inspect output at any time."
        )
        return ToolResult(
            content=content,
            is_error=False,
            title=f"$ {data.command} [background → {task_id}]",
        )

    # ------------------------------------------------------------------
    # Foreground execution — with streaming progress
    # ------------------------------------------------------------------

    async def _run_foreground(self, data: BashInput, ctx: ToolContext) -> ToolResult:
        # Extend the timeout automatically for known server commands so the
        # user gets a helpful message rather than a cryptic timeout error.
        timeout = data.timeout
        if _is_server_command(data.command) and timeout == DEFAULT_TIMEOUT:
            timeout = 600  # 10 minutes for servers running in foreground

        # Platform-specific kwargs for process group management.
        extra: dict = {}
        if platform.system() != "Windows":
            # Start the process in its own session so kill_process_tree can
            # send SIGTERM to the entire group.
            extra["preexec_fn"] = os.setsid

        try:
            proc = await asyncio.create_subprocess_shell(
                data.command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(ctx.cwd),
                env=get_noninteractive_env(),
                **extra,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("failed to start command %r: %s", data.command, exc)
            return ToolResult.error(f"Failed to start command: {exc}")

        # Stream output chunks, pushing progress to the UI at intervals.
        lines: list[str] = []
        last_progress = time.monotonic()
        deadline = time.monotonic() + timeout

        try:
            assert proc.stdout is not None  # guaranteed by PIPE
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    kill_process_tree(proc)
                    await proc.wait()
                    hint = (
                        "\n\nTip: if this is a long-running server or watcher, "
                        "re-run with run_in_background=true so it starts without blocking."
                        if _is_server_command(data.command)
                        else ""
                    )
                    return ToolResult.error(
                        f"Command timed out after {timeout}s.{hint}",
                        title=f"$ {data.command}",
                    )

                try:
                    chunk = await asyncio.wait_for(
                        proc.stdout.read(4096), timeout=min(remaining, 1.0)
                    )
                except (TimeoutError, asyncio.TimeoutError):
                    if proc.returncode is not None:
                        break
                    continue

                if not chunk:
                    break  # EOF

                text = chunk.decode("utf-8", errors="replace")
                lines.append(text)

                # Push progress to the UI at intervals.
                now = time.monotonic()
                if ctx.on_progress and (now - last_progress) >= PROGRESS_INTERVAL:
                    last_progress = now
                    partial = "".join(lines[-50:])
                    try:
                        await ctx.on_progress(partial)
                    except Exception:  # noqa: BLE001 — progress is best-effort
                        pass

        except asyncio.CancelledError:
            kill_process_tree(proc)
            raise

        # Wait for the process to fully exit.
        await proc.wait()

        output = _truncate("".join(lines)) if lines else ""
        code = proc.returncode or 0
        is_error = code != 0
        content = output if output else "(no output)"
        if is_error:
            content = f"{content}\n\n[exit code: {code}]"

        logger.debug("bash %r finished (exit %d, %d lines)", data.command, code, len(lines))

        return ToolResult(
            content=content,
            is_error=is_error,
            title=f"$ {data.command}"
            + ("" if not is_error else f"  (exit {code})"),
            display=output,
        )
