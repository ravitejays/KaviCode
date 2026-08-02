"""Task tool - delegate a focused sub-task to a sub-agent."""

from __future__ import annotations

from pydantic import BaseModel, Field

from kavi.subagents.runner import AGENT_TYPES, resolve_agent_type
from kavi.tools.base import Tool, ToolContext, ToolResult


class TaskInput(BaseModel):
    description: str = Field(description="A short (3-5 word) description of the task.")
    prompt: str = Field(description="The detailed task for the sub-agent to perform autonomously.")
    subagent_type: str = Field(
        default="general",
        description="Which sub-agent to use: " + ", ".join(AGENT_TYPES.keys()),
    )


class TaskTool(Tool):
    name = "Task"
    description = """
    Delegate a focused, self-contained task to a sub-agent that runs its own tool-call loop
    with a restricted toolset, then returns a summary. Use for open-ended searches or
    multi-step sub-tasks. Provide a complete, standalone prompt: the sub-agent does not see
    the parent conversation.
    """
    InputModel = TaskInput
    # The sub-agent handles its own per-tool permissions; launching one is safe.
    is_read_only = True

    def render_call(self, data: TaskInput) -> str:  # type: ignore[override]
        return f"Task[{data.subagent_type}]: {data.description}"

    async def run(self, data: TaskInput, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        if ctx.run_subagent is None:
            return ToolResult.error("Sub-agents are not available in this context.")
        spec = resolve_agent_type(data.subagent_type)
        try:
            summary = await ctx.run_subagent(
                data.prompt, spec["tools"], spec.get("system_suffix")
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult.error(f"Sub-agent failed: {exc}")
        return ToolResult(
            content=summary or "(sub-agent produced no output)",
            title=f"Task[{data.subagent_type}] complete",
            display=summary,
        )
