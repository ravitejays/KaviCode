"""Grep tool - search file contents using ripgrep (with a pure-Python fallback)."""

from __future__ import annotations

import asyncio
import re
import shutil
from typing import Literal

from pydantic import BaseModel, Field

from kavi.tools.base import Tool, ToolContext, ToolResult

MAX_MATCHES = 200


class GrepInput(BaseModel):
    pattern: str = Field(description="Regular expression to search for.")
    path: str = Field(default=".", description="File or directory to search in.")
    glob: str | None = Field(default=None, description="Glob filter, e.g. '*.py'.")
    case_insensitive: bool = Field(default=False, description="Case-insensitive match.")
    output_mode: Literal["content", "files_with_matches", "count"] = Field(
        default="content", description="What to return."
    )


class GrepTool(Tool):
    name = "Grep"
    description = """
    Search file contents with a regular expression. Uses ripgrep when available. Filter by
    a glob (e.g. '*.ts'), choose case sensitivity, and select an output mode: matching lines
    ('content'), matching file paths ('files_with_matches'), or per-file counts ('count').
    """
    InputModel = GrepInput
    is_read_only = True
    is_concurrency_safe = True

    def permission_subject(self, data: GrepInput) -> str:  # type: ignore[override]
        return data.path

    def render_call(self, data: GrepInput) -> str:  # type: ignore[override]
        return f"Grep '{data.pattern}' in {data.path}"

    async def run(self, data: GrepInput, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        if shutil.which("rg"):
            return await self._run_rg(data, ctx)
        return self._run_python(data, ctx)

    async def _run_rg(self, data: GrepInput, ctx: ToolContext) -> ToolResult:
        args = ["rg", "--color", "never"]
        if data.case_insensitive:
            args.append("-i")
        if data.output_mode == "files_with_matches":
            args.append("-l")
        elif data.output_mode == "count":
            args.append("-c")
        else:
            args.extend(["-n", "--heading"])
        if data.glob:
            args.extend(["--glob", data.glob])
        args.extend(["--", data.pattern, data.path])

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(ctx.cwd),
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode not in (0, 1):
            return ToolResult.error(stderr.decode(errors="replace") or "ripgrep failed")

        out = stdout.decode(errors="replace").strip()
        if not out:
            return ToolResult(content="No matches found.", title=f"Grep '{data.pattern}'")
        lines = out.splitlines()
        truncated = len(lines) > MAX_MATCHES
        body = "\n".join(lines[:MAX_MATCHES])
        if truncated:
            body += f"\n\n... {len(lines) - MAX_MATCHES} more results"
        return ToolResult(content=body, title=f"Grep '{data.pattern}' ({len(lines)} results)")

    def _run_python(self, data: GrepInput, ctx: ToolContext) -> ToolResult:
        flags = re.IGNORECASE if data.case_insensitive else 0
        try:
            regex = re.compile(data.pattern, flags)
        except re.error as exc:
            return ToolResult.error(f"Invalid regex: {exc}")

        root = self.resolve_path(ctx.cwd, data.path)
        candidates = root.rglob(data.glob) if data.glob else root.rglob("*")
        results: list[str] = []
        files_with: list[str] = []
        counts: dict[str, int] = {}
        for fp in candidates:
            if not fp.is_file():
                continue
            if any(part in {".git", "node_modules", "__pycache__", ".venv"} for part in fp.parts):
                continue
            try:
                text = fp.read_text(encoding="utf-8", errors="ignore")
            except Exception:  # noqa: BLE001
                continue
            matched = False
            for lineno, line in enumerate(text.splitlines(), 1):
                if regex.search(line):
                    matched = True
                    counts[str(fp)] = counts.get(str(fp), 0) + 1
                    if data.output_mode == "content":
                        results.append(f"{fp}:{lineno}:{line}")
                        if len(results) >= MAX_MATCHES:
                            break
            if matched:
                files_with.append(str(fp))
            if len(results) >= MAX_MATCHES:
                break

        if data.output_mode == "files_with_matches":
            body = "\n".join(files_with) or "No matches found."
        elif data.output_mode == "count":
            body = "\n".join(f"{k}:{v}" for k, v in counts.items()) or "No matches found."
        else:
            body = "\n".join(results) or "No matches found."
        return ToolResult(content=body, title=f"Grep '{data.pattern}'")
