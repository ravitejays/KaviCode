"""Wrap MCP server tools as Kavi tools."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from kavi.messages import ToolSchema
from kavi.tools.base import Tool, ToolContext, ToolResult


class _RawArgs(BaseModel):
    """Permissive model that accepts and preserves arbitrary keys."""

    model_config = ConfigDict(extra="allow")


class McpTool(Tool):
    """A Kavi tool backed by a tool exposed by a connected MCP server."""

    is_read_only = False  # external side effects unknown -> default to asking

    def __init__(
        self,
        *,
        server_name: str,
        tool_name: str,
        description: str,
        input_schema: dict[str, Any],
        call,  # async callable: (tool_name, args) -> str
    ) -> None:
        self.server_name = server_name
        self.tool_name = tool_name
        # Namespaced to avoid collisions with built-ins and other servers.
        self.name = f"mcp__{server_name}__{tool_name}"
        self.description = description or f"MCP tool '{tool_name}' from server '{server_name}'."
        self._input_schema = input_schema or {"type": "object", "properties": {}}
        self._call = call
        self.InputModel = _RawArgs

    def to_schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name, description=self.description.strip(), input_schema=self._input_schema
        )

    def validate(self, raw: dict[str, Any]) -> BaseModel:
        return _RawArgs.model_validate(raw or {})

    def permission_subject(self, data: BaseModel) -> str:  # type: ignore[override]
        return self.tool_name

    def render_call(self, data: BaseModel) -> str:  # type: ignore[override]
        return f"{self.server_name}.{self.tool_name}"

    async def run(self, data: BaseModel, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        args = data.model_dump()
        try:
            text = await self._call(self.tool_name, args)
        except Exception as exc:  # noqa: BLE001
            return ToolResult.error(f"MCP call failed: {exc}")
        return ToolResult(content=text or "(no output)", title=self.render_call(data))
