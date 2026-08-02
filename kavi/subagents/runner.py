"""Sub-agent runner.

A sub-agent is a nested :class:`AgentEngine` with its own conversation and a restricted
toolset. It shares the parent's provider and permission engine (so approvals and grants
carry over) and reports through the parent's callbacks so nested activity stays visible.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kavi.agent.context import Conversation
from kavi.agent.prompts import build_system_prompt

if TYPE_CHECKING:
    from kavi.agent.engine import AgentEngine

# Read-only investigation tools.
_READ_TOOLS = ["Read", "Grep", "Glob", "LS", "WebSearch", "WebFetch", "ViewImage", "Skill"]
# Full mutating toolset (no Task, to avoid unbounded recursion).
_CODE_TOOLS = [
    "Read", "Write", "Edit", "MultiEdit", "Bash", "Grep", "Glob", "LS",
    "WebSearch", "WebFetch", "ViewImage", "TodoWrite", "Skill",
]

AGENT_TYPES: dict[str, dict[str, Any]] = {
    "general": {
        "description": "General-purpose agent with the full toolset for multi-step tasks.",
        "tools": _CODE_TOOLS,
        "system_suffix": "You are a focused sub-agent. Complete the task and report a concise summary.",
    },
    "explore": {
        "description": "Read-only agent for searching and understanding the codebase.",
        "tools": _READ_TOOLS,
        "system_suffix": (
            "You are a read-only exploration sub-agent. Investigate and return findings. "
            "Do not attempt to modify files."
        ),
    },
    "code": {
        "description": "Agent that can read, write, edit, and run commands to implement changes.",
        "tools": _CODE_TOOLS,
        "system_suffix": (
            "You are an implementation sub-agent. Make complete, production-ready changes "
            "with ZERO placeholders or stubs. Install dependencies and verify your changes before finishing."
        ),
    },
}


def resolve_agent_type(name: str) -> dict[str, Any]:
    return AGENT_TYPES.get(name, AGENT_TYPES["general"])


async def run_subagent(
    *,
    parent: AgentEngine,
    prompt: str,
    tool_names: list[str],
    system_suffix: str | None = None,
    agent_name: str | None = None,
    team_context: str | None = None,
) -> str:
    from kavi.agent.engine import AgentEngine

    if agent_name and team_context:
        prefix = f"You are '{agent_name}', a specialized agent on Team '{team_context}'.\n"
        system_suffix = prefix + (system_suffix or "")
    elif agent_name:
        prefix = f"You are '{agent_name}', a specialized sub-agent.\n"
        system_suffix = prefix + (system_suffix or "")

    system = build_system_prompt(
        parent.cwd,
        suffix=system_suffix,
        provider=parent.config.provider.value,
        model=parent.config.resolved_model(),
    )
    conversation = Conversation(system_prompt=system)

    sub = AgentEngine(
        config=parent.config,
        provider=parent.provider,
        registry=parent.registry,
        conversation=conversation,
        permissions=parent.permissions,
        cwd=parent.cwd,
        callbacks=parent.callbacks,
        tool_names=tool_names,
        skills=parent.skills,
    )
    return await sub.run(prompt)
