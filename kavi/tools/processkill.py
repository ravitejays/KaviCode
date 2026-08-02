"""Cross-platform process-tree kill helper.

On Unix we use ``os.killpg`` to kill the whole process group. On Windows we
use ``taskkill /F /T`` to kill the process tree. Both approaches ensure child
processes spawned by shells (``npm run dev``, ``webpack serve``, etc.) don't
survive their parent.

Inspired by Claude Code's ``killShellTasks.ts`` which does the same via
Node's ``child_process.spawn("taskkill", ...)``.
"""

from __future__ import annotations

import os
import platform
import signal


def kill_process_tree(proc) -> None:  # noqa: ANN001 — accepts asyncio.subprocess.Process
    """Kill *proc* and all of its child processes.

    Works cross-platform:
    - **Unix**: sends ``SIGTERM`` to the process group (if the process was
      started with ``start_new_session=True``), falling back to
      ``proc.terminate()``.
    - **Windows**: runs ``taskkill /F /T /PID <pid>`` to forcibly kill the
      process tree, falling back to ``proc.kill()``.

    Silently swallows all errors — the process may already be dead.
    """
    pid = proc.pid
    if pid is None:
        return

    try:
        if platform.system() == "Windows":
            _kill_windows(pid)
        else:
            _kill_unix(pid, proc)
    except Exception:  # noqa: BLE001  — best-effort cleanup
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


def _kill_unix(pid: int, proc) -> None:  # noqa: ANN001
    """Send SIGTERM to the process group, or fall back to proc.terminate."""
    try:
        pgid = os.getpgid(pid)
        os.killpg(pgid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001
            pass


def _kill_windows(pid: int) -> None:
    """Use ``taskkill /F /T`` to kill the entire process tree."""
    import subprocess

    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        # taskkill not available — fall back handled by the caller
        raise
