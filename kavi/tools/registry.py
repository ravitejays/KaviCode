"""Tool registry - holds all available tools and exposes their schemas."""

from __future__ import annotations

from kavi.messages import ToolSchema
from kavi.tools.base import Tool


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def schemas(self, only: list[str] | None = None) -> list[ToolSchema]:
        tools = self.all() if only is None else [self._tools[n] for n in only if n in self._tools]
        return [t.to_schema() for t in tools]

    def subset(self, names: list[str]) -> ToolRegistry:
        """A new registry containing only the named tools (used for sub-agents)."""
        r = ToolRegistry()
        for n in names:
            if n in self._tools:
                r.register(self._tools[n])
        return r


def build_builtin_registry() -> ToolRegistry:
    """Registry populated with the built-in tools (no Task/MCP tools yet)."""
    from kavi.tools.bash import BashTool
    from kavi.tools.edit import EditTool
    from kavi.tools.glob import GlobTool
    from kavi.tools.grep import GrepTool
    from kavi.tools.ls import LSTool
    from kavi.tools.multiedit import MultiEditTool
    from kavi.tools.read import ReadTool
    from kavi.tools.skill import SkillTool
    from kavi.tools.todo import TodoWriteTool
    from kavi.tools.viewimage import ViewImageTool
    from kavi.tools.webfetch import WebFetchTool
    from kavi.tools.websearch import WebSearchTool
    from kavi.tools.write import WriteTool
    from kavi.tools.browser import (
        BrowserNavigateTool,
        BrowserScreenshotTool,
        BrowserClickTool,
        BrowserFillTool,
        BrowserJsTool,
        BrowserClearSessionTool,
    )
    from kavi.tools.swarm import AgentSpawnTool, TeamCreateTool
    from kavi.tools.security import SecurityScanTool, SecurityFindingsTool

    registry = ToolRegistry()
    for tool_cls in (
        ReadTool,
        WriteTool,
        EditTool,
        MultiEditTool,
        BashTool,
        GrepTool,
        GlobTool,
        LSTool,
        WebSearchTool,
        WebFetchTool,
        ViewImageTool,
        TodoWriteTool,
        SkillTool,
        BrowserNavigateTool,
        BrowserScreenshotTool,
        BrowserClickTool,
        BrowserFillTool,
        BrowserJsTool,
        BrowserClearSessionTool,
        AgentSpawnTool,
        TeamCreateTool,
        SecurityScanTool,
        SecurityFindingsTool,
    ):
        registry.register(tool_cls())
    return registry
