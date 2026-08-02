"""MultiEdit tool - apply several sequential edits to one file atomically."""

from __future__ import annotations

from pydantic import BaseModel, Field

from kavi.tools.base import Tool, ToolContext, ToolResult
from kavi.tools.diagnostics import diagnostics_suffix
from kavi.tools.diffutil import count_changes, unified_diff


class EditOp(BaseModel):
    old_string: str = Field(description="Exact text to replace.")
    new_string: str = Field(description="Replacement text.")
    replace_all: bool = Field(default=False)


class MultiEditInput(BaseModel):
    file_path: str = Field(description="Path to the file to edit.")
    edits: list[EditOp] = Field(description="Edits applied in order to the file.")


class MultiEditTool(Tool):
    name = "MultiEdit"
    description = """
    Apply multiple exact string replacements to a single file in one atomic operation.
    Edits are applied sequentially; each edit operates on the result of the previous one.
    If any edit fails, no changes are written.
    """
    InputModel = MultiEditInput

    def permission_subject(self, data: MultiEditInput) -> str:  # type: ignore[override]
        return data.file_path

    def render_call(self, data: MultiEditInput) -> str:  # type: ignore[override]
        return f"MultiEdit {data.file_path} ({len(data.edits)} edits)"

    async def run(self, data: MultiEditInput, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        path = self.resolve_path(ctx.cwd, data.file_path)
        if not path.exists():
            return ToolResult.error(f"File not found: {path}")
        if not data.edits:
            return ToolResult.error("No edits provided.")

        try:
            original = path.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            return ToolResult.error(f"Could not read {path}: {exc}")

        working = original
        for i, op in enumerate(data.edits):
            if op.old_string == op.new_string:
                return ToolResult.error(f"Edit #{i + 1}: old_string equals new_string.")
            count = working.count(op.old_string)
            if count == 0:
                return ToolResult.error(f"Edit #{i + 1}: old_string not found.")
            if count > 1 and not op.replace_all:
                return ToolResult.error(
                    f"Edit #{i + 1}: old_string not unique ({count}). Use replace_all or add context."
                )
            working = (
                working.replace(op.old_string, op.new_string)
                if op.replace_all
                else working.replace(op.old_string, op.new_string, 1)
            )

        try:
            path.write_text(working, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            return ToolResult.error(f"Could not write {path}: {exc}")

        added, removed = count_changes(original, working)
        suffix = await diagnostics_suffix(ctx, path)
        return ToolResult(
            content=f"Applied {len(data.edits)} edits to {path} (+{added} -{removed}).{suffix}",
            title=f"MultiEdit {data.file_path} ({len(data.edits)} edits, +{added} -{removed})",
            display=unified_diff(original, working, data.file_path),
        )
