"""Read tool - read a file from disk with line numbers."""

from __future__ import annotations

from pydantic import BaseModel, Field

from kavi.tools.base import Tool, ToolContext, ToolResult

MAX_LINES = 2000
MAX_LINE_LEN = 2000


class ReadInput(BaseModel):
    file_path: str = Field(description="Absolute or relative path to the file to read.")
    offset: int = Field(default=0, description="0-based line number to start reading from.")
    limit: int = Field(default=MAX_LINES, description="Maximum number of lines to read.")


class ReadTool(Tool):
    name = "Read"
    description = """
    Read a file from the local filesystem. Returns the file contents with line numbers in
    a `cat -n` style (right-aligned line number, a tab, then the line). Use `offset` and
    `limit` to page through very large files. Prefer this over running `cat` via Bash.
    """
    InputModel = ReadInput
    is_read_only = True
    is_concurrency_safe = True

    def permission_subject(self, data: ReadInput) -> str:  # type: ignore[override]
        return data.file_path

    def render_call(self, data: ReadInput) -> str:  # type: ignore[override]
        return f"Read {data.file_path}"

    async def run(self, data: ReadInput, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        path = self.resolve_path(ctx.cwd, data.file_path)
        if not path.exists():
            return ToolResult.error(f"File not found: {path}")
        if path.is_dir():
            return ToolResult.error(f"Path is a directory, not a file: {path}")
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            return ToolResult.error(f"Could not read {path}: {exc}")

        if text == "":
            return ToolResult(content="(file is empty)", title=f"Read {data.file_path}")

        lines = text.splitlines()
        start = max(0, data.offset)
        end = min(len(lines), start + max(1, data.limit))
        numbered = []
        for i in range(start, end):
            line = lines[i]
            if len(line) > MAX_LINE_LEN:
                line = line[:MAX_LINE_LEN] + " ... (truncated)"
            numbered.append(f"{i + 1:6d}\t{line}")

        body = "\n".join(numbered)
        suffix = ""
        if end < len(lines):
            suffix = f"\n\n... {len(lines) - end} more lines (use offset={end} to continue)"
        return ToolResult(
            content=body + suffix,
            title=f"Read {data.file_path} ({end - start} lines)",
        )
