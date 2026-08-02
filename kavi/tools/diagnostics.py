"""Post-edit diagnostics - catch obvious breakage right after a write.

A common reason agents loop is that they do not notice they broke a file until a
much later test run. This module gives cheap, immediate feedback: after an edit
or write, it runs a fast check on the changed file and returns any errors, which
the file tools append to their result so the model can self-correct on the very
next step.

Design goals (matching Kavi's zero-friction ethos):
  * **no required dependencies** - Python syntax is checked with the stdlib
    ``compile`` builtin and JSON with ``json.loads``;
  * **use better tools when present** - if ``ruff`` / ``pyflakes`` (Python),
    ``node`` (JS), or ``tsc`` (TS) are on PATH they enrich the check, but their
    absence is never an error;
  * **fast and bounded** - every external check has a short timeout and its
    output is capped, so diagnostics never dominate a turn.

It is a lightweight stand-in for a full LSP client: the same self-correction
value, none of the language-server machinery.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kavi.tools.base import ToolContext

TIMEOUT_SECONDS = 15
MAX_LINES = 12


async def diagnostics_suffix(ctx: ToolContext, path: Path) -> str:
    """Run diagnostics for ``path`` if enabled in config; return a text block.

    Convenience for file-writing tools: honours ``config.post_edit_diagnostics``
    and swallows all errors so a diagnostics failure can never break an edit.
    """
    if not getattr(ctx.config, "post_edit_diagnostics", True):
        return ""
    try:
        return format_issues(await check(path))
    except Exception:  # noqa: BLE001
        return ""


async def check(path: Path) -> list[str]:
    """Return diagnostic messages for ``path`` (empty if clean/unsupported)."""
    try:
        if not path.is_file():
            return []
        ext = path.suffix.lower()
        if ext == ".py":
            return await _check_python(path)
        if ext == ".json":
            return _check_json(path)
        if ext in (".js", ".mjs", ".cjs"):
            return await _check_node(path)
        if ext in (".ts", ".tsx"):
            return await _check_tsc(path)
    except Exception:  # noqa: BLE001 - diagnostics must never break an edit
        return []
    return []


def format_issues(issues: list[str]) -> str:
    """Render issues as a compact block to append to a tool result, or ''."""
    if not issues:
        return ""
    shown = issues[:MAX_LINES]
    extra = len(issues) - len(shown)
    body = "\n".join(f"  - {line}" for line in shown)
    if extra > 0:
        body += f"\n  ... (+{extra} more)"
    return "\n\nDiagnostics found issues in the file you just changed - fix them:\n" + body


# --------------------------------------------------------------------- Python
async def _check_python(path: Path) -> list[str]:
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    # 1) Syntax check with the stdlib (always available, catches the worst breaks).
    try:
        compile(src, str(path), "exec")
    except SyntaxError as exc:
        where = f"line {exc.lineno}" if exc.lineno else "?"
        return [f"SyntaxError: {exc.msg} ({where})"]
    # 2) Richer linting if a linter is installed (best-effort).
    if shutil.which("ruff"):
        return await _run(["ruff", "check", "--quiet", str(path)])
    if shutil.which("pyflakes"):
        return await _run(["pyflakes", str(path)])
    return []


# ----------------------------------------------------------------------- JSON
def _check_json(path: Path) -> list[str]:
    try:
        json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        return [f"JSON parse error: {exc.msg} (line {exc.lineno}, col {exc.colno})"]
    except OSError:
        return []
    return []


# ------------------------------------------------------------- JS / TS (opt.)
async def _check_node(path: Path) -> list[str]:
    if not shutil.which("node"):
        return []
    return await _run(["node", "--check", str(path)])


async def _check_tsc(path: Path) -> list[str]:
    if not shutil.which("tsc"):
        return []
    return await _run(["tsc", "--noEmit", "--pretty", "false", str(path)])


# --------------------------------------------------------------------- runner
async def _run(cmd: list[str]) -> list[str]:
    """Run a checker; return its output lines on failure, [] on success/error."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except OSError:
        return []
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=TIMEOUT_SECONDS)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return []
    if proc.returncode == 0:
        return []
    out = stdout.decode("utf-8", errors="replace") if stdout else ""
    return [ln for ln in out.splitlines() if ln.strip()][:MAX_LINES]
