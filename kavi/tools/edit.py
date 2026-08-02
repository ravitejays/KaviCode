"""Edit tool - exact string replacement in a file."""

from __future__ import annotations

from pydantic import BaseModel, Field

from kavi.tools.base import Tool, ToolContext, ToolResult
from kavi.tools.diagnostics import diagnostics_suffix
from kavi.tools.diffutil import count_changes, unified_diff


class EditInput(BaseModel):
    file_path: str = Field(description="Path to the file to edit.")
    old_string: str = Field(description="Exact text to replace (must be unique unless replace_all).")
    new_string: str = Field(description="Replacement text. Must differ from old_string.")
    replace_all: bool = Field(default=False, description="Replace every occurrence.")


class EditTool(Tool):
    name = "Edit"
    description = """
    Perform an exact string replacement in a file. `old_string` must match the file
    contents exactly (including whitespace) and must be unique unless `replace_all` is
    true. Read the file first so your `old_string` is accurate.
    """
    InputModel = EditInput

    def permission_subject(self, data: EditInput) -> str:  # type: ignore[override]
        return data.file_path

    def render_call(self, data: EditInput) -> str:  # type: ignore[override]
        return f"Edit {data.file_path}"

    async def run(self, data: EditInput, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        if data.old_string == data.new_string:
            return ToolResult.error("old_string and new_string are identical; nothing to do.")

        path = self.resolve_path(ctx.cwd, data.file_path)
        if not path.exists():
            return ToolResult.error(f"File not found: {path}")

        try:
            original = path.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            return ToolResult.error(f"Could not read {path}: {exc}")

        count = original.count(data.old_string)
        if count == 0:
            return ToolResult.error("old_string not found in file.")
        if count > 1 and not data.replace_all:
            return ToolResult.error(
                f"old_string is not unique ({count} matches). Add more context or set "
                "replace_all=true."
            )

        if data.replace_all:
            updated = original.replace(data.old_string, data.new_string)
        else:
            updated = original.replace(data.old_string, data.new_string, 1)

        try:
            path.write_text(updated, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            return ToolResult.error(f"Could not write {path}: {exc}")

        added, removed = count_changes(original, updated)
        suffix = await diagnostics_suffix(ctx, path)
        return ToolResult(
            content=f"Edited {path} (+{added} -{removed}).{suffix}",
            title=f"Edit {data.file_path} (+{added} -{removed})",
            display=unified_diff(original, updated, data.file_path),
        )
