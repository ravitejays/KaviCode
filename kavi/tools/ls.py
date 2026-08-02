"""LS tool - list the contents of a directory."""

from __future__ import annotations

from pydantic import BaseModel, Field

from kavi.tools.base import Tool, ToolContext, ToolResult

MAX_ENTRIES = 400


class LSInput(BaseModel):
    path: str = Field(default=".", description="Directory to list.")
    all: bool = Field(default=False, description="Include hidden entries (dotfiles).")


class LSTool(Tool):
    name = "LS"
    description = """
    List the files and directories at a path. Directories are suffixed with '/'. Hidden
    entries are excluded unless `all` is true.
    """
    InputModel = LSInput
    is_read_only = True
    is_concurrency_safe = True

    def permission_subject(self, data: LSInput) -> str:  # type: ignore[override]
        return data.path

    def render_call(self, data: LSInput) -> str:  # type: ignore[override]
        return f"LS {data.path}"

    async def run(self, data: LSInput, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        path = self.resolve_path(ctx.cwd, data.path)
        if not path.exists():
            return ToolResult.error(f"Path not found: {path}")
        if not path.is_dir():
            return ToolResult.error(f"Not a directory: {path}")

        entries = []
        for child in sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
            if not data.all and child.name.startswith("."):
                continue
            entries.append(child.name + ("/" if child.is_dir() else ""))

        if not entries:
            return ToolResult(content="(empty directory)", title=f"LS {data.path}")
        truncated = len(entries) > MAX_ENTRIES
        body = "\n".join(entries[:MAX_ENTRIES])
        if truncated:
            body += f"\n\n... {len(entries) - MAX_ENTRIES} more entries"
        return ToolResult(content=body, title=f"LS {data.path} ({len(entries)} entries)")
