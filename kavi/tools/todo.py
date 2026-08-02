"""TodoWrite tool - a lightweight task planner the agent can use.

The model passes the full task list each time it updates progress. The list is
stored on the tool context (so the UI can render it) and echoed back compactly so
the model keeps sight of its plan. Read-only with respect to the workspace.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from kavi.tools.base import Tool, ToolContext, ToolResult

TodoStatus = Literal["pending", "in_progress", "completed", "cancelled"]

_MARK = {
    "pending": "[ ]",
    "in_progress": "[~]",
    "completed": "[x]",
    "cancelled": "[-]",
}


class TodoItem(BaseModel):
    content: str = Field(description="What the step is.")
    status: TodoStatus = Field(default="pending")


class TodoWriteInput(BaseModel):
    todos: list[TodoItem] = Field(
        description="The full task list. Keep exactly one item 'in_progress'."
    )


class TodoWriteTool(Tool):
    name = "TodoWrite"
    description = """
    Create or update a structured task list to plan and track multi-step work. Pass the FULL
    list each time you update it. Keep exactly one item 'in_progress' at a time, and mark
    items 'completed' as you finish them. Use this for any non-trivial task so the user can
    follow your progress. Statuses: pending, in_progress, completed, cancelled.
    """
    InputModel = TodoWriteInput
    is_read_only = True

    def render_call(self, data: TodoWriteInput) -> str:  # type: ignore[override]
        done = sum(1 for t in data.todos if t.status == "completed")
        return f"TodoWrite ({done}/{len(data.todos)} done)"

    async def run(self, data: TodoWriteInput, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        todos = [{"content": t.content, "status": t.status} for t in data.todos]
        ctx.extras["todos"] = todos
        # Let the UI render the list if it wants to (optional callback).
        render = ctx.extras.get("render_todos")
        if callable(render):
            try:
                render(todos)
            except Exception:  # noqa: BLE001 - rendering must never break the tool
                pass

        rendered = "\n".join(f"{_MARK[t['status']]} {t['content']}" for t in todos)
        done = sum(1 for t in todos if t["status"] == "completed")
        return ToolResult(
            content=f"Todo list updated ({done}/{len(todos)} completed):\n{rendered}",
            title=f"TodoWrite ({done}/{len(todos)} done)",
            display=rendered,
            ui_payload={"todos": todos},
        )
