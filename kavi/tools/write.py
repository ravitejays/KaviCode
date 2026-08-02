"""Write tool - create or overwrite a file."""

from __future__ import annotations

from pydantic import BaseModel, Field

from kavi.tools.base import Tool, ToolContext, ToolResult
from kavi.tools.diagnostics import diagnostics_suffix


class WriteInput(BaseModel):
    file_path: str = Field(description="Path to the file to write.")
    content: str = Field(description="The full contents to write to the file.")


class WriteTool(Tool):
    name = "Write"
    description = """
    Write a file to the local filesystem, creating parent directories as needed. Overwrites
    the file if it already exists. Prefer the Edit tool for modifying existing files.
    """
    InputModel = WriteInput

    def permission_subject(self, data: WriteInput) -> str:  # type: ignore[override]
        return data.file_path

    def render_call(self, data: WriteInput) -> str:  # type: ignore[override]
        n = data.content.count("\n") + 1
        return f"Write {data.file_path} ({n} lines)"

    async def run(self, data: WriteInput, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        path = self.resolve_path(ctx.cwd, data.file_path)
        existed = path.exists()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(data.content, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            return ToolResult.error(f"Could not write {path}: {exc}")

        verb = "Updated" if existed else "Created"
        n = data.content.count("\n") + 1
        suffix = await diagnostics_suffix(ctx, path)
        return ToolResult(
            content=f"{verb} {path} ({n} lines).{suffix}",
            title=f"{verb} {data.file_path}",
            display=data.content,
        )
