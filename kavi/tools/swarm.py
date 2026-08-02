"""Swarm tools - true parallel multi-agent orchestration."""

from __future__ import annotations

import asyncio
import textwrap

from pydantic import BaseModel, Field

from kavi.subagents.runner import AGENT_TYPES, resolve_agent_type
from kavi.tools.base import Tool, ToolContext, ToolResult


class TeamCreateInput(BaseModel):
    team_name: str = Field(description="Name of the team.")
    description: str = Field(description="Description of the team's overarching goal.")

class TeamCreateTool(Tool):
    name = "TeamCreate"
    description = """
    Establish a team context for orchestrating multiple agents. 
    Use this to declare a team before spawning agents to work on complex projects.
    """
    InputModel = TeamCreateInput
    is_read_only = False

    def render_call(self, data: TeamCreateInput) -> str:  # type: ignore[override]
        return f"TeamCreate[{data.team_name}]"

    async def run(self, data: TeamCreateInput, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        team_file = ctx.cwd / f"{data.team_name.lower().replace(' ', '_')}_team.md"
        team_file.write_text(f"# Team: {data.team_name}\n\nGoal: {data.description}\n", encoding="utf-8")

        return ToolResult(
            content=f"Team '{data.team_name}' created. Team file written to {team_file.name}. "
                    "You can now spawn agents using AgentSpawn and pass this team name as the context.",
            title=f"Team {data.team_name} Created",
        )


class AgentSpawnInput(BaseModel):
    agent_name: str = Field(description="A distinct name for this agent (e.g. 'Researcher-1').")
    prompt: str = Field(description="The detailed task for the sub-agent to perform autonomously.")
    subagent_type: str = Field(
        default="general",
        description="Which sub-agent to use: " + ", ".join(AGENT_TYPES.keys()),
    )
    team_context: str | None = Field(
        default=None, 
        description="The name of the team this agent belongs to (if any)."
    )

async def _background_runner(
    queue: asyncio.Queue[str], 
    run_func, 
    prompt: str, 
    tools: list[str], 
    suffix: str | None, 
    agent_name: str, 
    team_context: str | None
) -> None:
    try:
        result = await run_func(
            prompt, 
            tools, 
            system_suffix=suffix,
            agent_name=agent_name,
            team_context=team_context
        )
        queue.put_nowait(f"BACKGROUND AGENT '{agent_name}' COMPLETED.\n\nResult:\n{result}")
    except Exception as exc:
        queue.put_nowait(f"BACKGROUND AGENT '{agent_name}' FAILED with error: {exc}")


class AgentSpawnTool(Tool):
    name = "AgentSpawn"
    description = textwrap.dedent("""\
        Spawn a sub-agent to work on a task IN THE BACKGROUND.
        Unlike Task, this tool returns immediately, allowing you to spawn multiple agents
        in parallel. When the agent finishes, you will receive a notification message
        with its final output. Use this to parallelize research, writing, or distinct
        implementation steps.
    """).strip()
    InputModel = AgentSpawnInput
    is_read_only = False

    def render_call(self, data: AgentSpawnInput) -> str:  # type: ignore[override]
        return f"AgentSpawn[{data.agent_name}]: {data.subagent_type}"

    async def run(self, data: AgentSpawnInput, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        if ctx.run_subagent is None:
            return ToolResult.error("Sub-agents are not available in this context.")
        
        queue = ctx.extras.get("notification_queue")
        if queue is None:
            return ToolResult.error("Background notification queue is not available.")

        spec = resolve_agent_type(data.subagent_type)
        
        # Fire and forget
        asyncio.create_task(
            _background_runner(
                queue, 
                ctx.run_subagent, 
                data.prompt, 
                spec["tools"], 
                spec.get("system_suffix"),
                data.agent_name,
                data.team_context
            )
        )

        return ToolResult(
            content=f"Agent '{data.agent_name}' spawned in the background. You will receive a notification when it completes its task.",
            title=f"Spawned {data.agent_name}",
        )
