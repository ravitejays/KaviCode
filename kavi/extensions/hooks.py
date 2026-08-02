"""Hooks - run shell commands on agent lifecycle events.

Configured in ``hooks.json`` under any source root:

    {
      "PreToolUse":  [{"matcher": "Bash|Write", "command": "./guard.sh"}],
      "PostToolUse": [{"matcher": "Write", "command": "prettier --write"}],
      "UserPromptSubmit": [{"command": "echo prompt >> log"}],
      "Stop": [{"command": "notify-send done"}]
    }

Each event carries a JSON payload (tool name, args, etc.) piped to the command on
stdin. ``matcher`` is a regex against the tool name (omitted => matches all). A
**non-zero exit from a PreToolUse hook blocks the tool** (its stderr/stdout is
returned to the model as the reason), mirroring Claude Code's deny semantics.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kavi.extensions import sources

EVENTS = ("PreToolUse", "PostToolUse", "UserPromptSubmit", "Stop")
_TIMEOUT = 30


@dataclass
class Hook:
    event: str
    matcher: str
    command: str
    _compiled: re.Pattern[str] | None = field(default=None, repr=False)

    def matches(self, tool_name: str) -> bool:
        if not self.matcher:
            return True
        if self._compiled is None:
            try:
                self._compiled = re.compile(self.matcher)
            except re.error:
                self._compiled = re.compile(re.escape(self.matcher))
        return bool(self._compiled.search(tool_name or ""))


@dataclass
class HookResult:
    blocked: bool = False
    message: str = ""


class HookRunner:
    def __init__(self, hooks: dict[str, list[Hook]]) -> None:
        self.hooks = hooks

    @classmethod
    def load(cls, workspace: Path) -> HookRunner:
        merged: dict[str, list[Hook]] = {e: [] for e in EVENTS}
        for path in sources.hook_config_files(workspace):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for event in EVENTS:
                for entry in data.get(event, []) or []:
                    cmd = entry.get("command")
                    if not cmd:
                        continue
                    merged[event].append(
                        Hook(event=event, matcher=str(entry.get("matcher", "")), command=cmd)
                    )
        return cls(merged)

    def any_for(self, event: str) -> bool:
        return bool(self.hooks.get(event))

    async def run(
        self,
        event: str,
        workspace: Path,
        tool_name: str = "",
        payload: dict[str, Any] | None = None,
    ) -> HookResult:
        """Run all hooks for ``event``. For PreToolUse, a non-zero exit blocks."""
        for hook in self.hooks.get(event, []):
            if not hook.matches(tool_name):
                continue
            data = json.dumps({"event": event, "tool": tool_name, **(payload or {})})
            try:
                proc = await asyncio.create_subprocess_shell(
                    hook.command,
                    cwd=str(workspace),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(data.encode()), timeout=_TIMEOUT
                )
            except (OSError, TimeoutError) as exc:
                if event == "PreToolUse":
                    return HookResult(blocked=True, message=f"hook failed: {exc}")
                continue
            if event == "PreToolUse" and proc.returncode != 0:
                msg = (
                    stderr.decode("utf-8", "replace").strip()
                    or stdout.decode("utf-8", "replace").strip()
                    or "blocked by hook"
                )
                return HookResult(blocked=True, message=msg)
        return HookResult()
