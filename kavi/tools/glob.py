"""Glob tool - find files by glob pattern, sorted by modification time."""

from __future__ import annotations

from pydantic import BaseModel, Field

from kavi.tools.base import Tool, ToolContext, ToolResult

MAX_RESULTS = 300
_IGNORE = {".git", "node_modules", "__pycache__", ".venv", "dist", "build"}


class GlobInput(BaseModel):
    pattern: str = Field(description="Glob pattern, e.g. '**/*.py' or 'src/**/*.ts'.")
    path: str = Field(default=".", description="Base directory to search from.")


class GlobTool(Tool):
    name = "Glob"
    description = """
    Find files matching a glob pattern. Supports '**' for recursive matching. Returns paths
    sorted by most-recently-modified first. Use this to locate files by name/pattern.
    """
    InputModel = GlobInput
    is_read_only = True
    is_concurrency_safe = True

    def permission_subject(self, data: GlobInput) -> str:  # type: ignore[override]
        return data.path

    def render_call(self, data: GlobInput) -> str:  # type: ignore[override]
        return f"Glob {data.pattern}"

    async def run(self, data: GlobInput, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        root = self.resolve_path(ctx.cwd, data.path)
        if not root.exists():
            return ToolResult.error(f"Path not found: {root}")

        matches = []
        for p in root.glob(data.pattern):
            if any(part in _IGNORE for part in p.parts):
                continue
            if p.is_file():
                try:
                    mtime = p.stat().st_mtime
                except OSError:
                    mtime = 0.0
                matches.append((mtime, str(p)))

        matches.sort(reverse=True)
        paths = [p for _, p in matches]
        if not paths:
            return ToolResult(content="No files matched.", title=f"Glob {data.pattern}")

        truncated = len(paths) > MAX_RESULTS
        body = "\n".join(paths[:MAX_RESULTS])
        if truncated:
            body += f"\n\n... {len(paths) - MAX_RESULTS} more files"
        return ToolResult(content=body, title=f"Glob {data.pattern} ({len(paths)} files)")
