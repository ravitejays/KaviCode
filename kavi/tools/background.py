"""Background task manager for the Bash tool.

When the agent runs a long-lived command (dev-servers, watchers, etc.) with
``run_in_background=True``, this module:

1. Spawns the subprocess and writes its combined stdout+stderr to a per-task
   log file under ``~/.kavi/tasks/<task_id>.log``.
2. Watches for the process to exit (in a fire-and-forget asyncio task).
3. Pushes a human-readable notification onto ``notification_queue`` when it
   does, so the agent engine can inject it as a user message and the model
   learns the outcome without polling.

Design notes
------------
- The queue is **per-engine-instance** (passed in at construction time in
  the ``extras`` dict) to avoid cross-contaminating parent ↔ sub-agent runs.
- On Unix, ``start_new_session=True`` is used so ``kill_process_tree`` can
  send SIGTERM to the entire process group.
- On Windows, ``CREATE_NEW_PROCESS_GROUP`` is set so the process can be
  cleanly killed later via ``taskkill /F /T``.
- ``get_task_tail`` reads the last *n* bytes of the log file so the model
  can inspect recent output without loading the whole file.
"""

from __future__ import annotations

import asyncio
import os
import platform
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path

from kavi.log import get_logger
from kavi.tools.processkill import kill_process_tree

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _tasks_dir() -> Path:
    """Directory where per-task log files are stored."""
    d = Path.home() / ".kavi" / "tasks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def task_log_path(task_id: str) -> Path:
    return _tasks_dir() / f"{task_id}.log"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class BackgroundTask:
    task_id: str
    command: str
    description: str
    log_path: Path
    proc: asyncio.subprocess.Process
    start_time: float = field(default_factory=time.time)
    # ``None`` while running, exit code once finished.
    exit_code: int | None = None


# Registry: task_id -> BackgroundTask
_registry: dict[str, BackgroundTask] = {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def spawn_background_task(
    command: str,
    description: str,
    cwd: Path,
    notification_queue: asyncio.Queue,  # type: ignore[type-arg]
) -> tuple[str, Path]:
    """Spawn *command* in the background and return ``(task_id, log_path)``.

    The process writes all output (stdout + stderr merged) to *log_path*.
    A completion notification is pushed onto *notification_queue* when the
    process exits.
    """
    task_id = secrets.token_hex(6)
    log_path = task_log_path(task_id)

    # Platform-specific kwargs so we can kill the process tree later.
    extra: dict = {}
    if platform.system() == "Windows":
        import subprocess
        extra["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        extra["start_new_session"] = True

    log_file = open(log_path, "wb")  # noqa: WPS515  (kept open by the watcher task)

    from kavi.tools.bash import get_noninteractive_env

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=log_file,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(cwd),
            env=get_noninteractive_env(),
            **extra,
        )
    except Exception as exc:  # noqa: BLE001
        log_file.close()
        logger.error("failed to start background command %r: %s", command, exc)
        raise RuntimeError(f"Failed to start background command: {exc}") from exc

    task = BackgroundTask(
        task_id=task_id,
        command=command,
        description=description,
        log_path=log_path,
        proc=proc,
    )
    _registry[task_id] = task

    logger.info("background task %s started: %s (pid %s)", task_id, command, proc.pid)

    # Fire-and-forget watcher using the modern create_task API (Python 3.12+).
    asyncio.get_running_loop().create_task(
        _watch(task, log_file, notification_queue),
        name=f"bg-watch-{task_id}",
    )

    return task_id, log_path


async def _watch(
    task: BackgroundTask,
    log_file,  # writable binary file
    notification_queue: asyncio.Queue,  # type: ignore[type-arg]
) -> None:
    """Wait for the process to exit then enqueue a completion notification."""
    try:
        code = await task.proc.wait()
    except Exception:  # noqa: BLE001
        code = -1
    finally:
        try:
            log_file.close()
        except Exception:  # noqa: BLE001
            pass

    task.exit_code = code
    status = "completed" if code == 0 else f"failed (exit {code})"

    logger.info("background task %s %s", task.task_id, status)

    msg = (
        f'Background command "{task.description}" {status}.\n'
        f"Output log: {task.log_path}"
    )
    await notification_queue.put(msg)


def get_task_tail(task_id: str, max_bytes: int = 4096) -> str:
    """Return the last *max_bytes* of a task's log as a UTF-8 string."""
    task = _registry.get(task_id)
    if task is None:
        return f"(no task with id {task_id!r})"
    path = task.log_path
    if not path.exists():
        return "(log file not yet created)"
    size = path.stat().st_size
    with open(path, "rb") as f:
        if size > max_bytes:
            f.seek(size - max_bytes)
        return f.read().decode("utf-8", errors="replace")


def list_tasks() -> list[BackgroundTask]:
    """Return all registered background tasks (running + completed)."""
    return list(_registry.values())


async def kill_task(task_id: str) -> bool:
    """Kill a running background task. Returns ``True`` if it was killed."""
    task = _registry.get(task_id)
    if task is None or task.exit_code is not None:
        return False
    logger.info("killing background task %s (pid %s)", task_id, task.proc.pid)
    try:
        kill_process_tree(task.proc)
    except Exception:  # noqa: BLE001
        logger.warning("failed to kill background task %s", task_id)
        return False
    return True
