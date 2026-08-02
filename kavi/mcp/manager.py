"""MCP client manager.

Connects to configured MCP servers, keeps their sessions alive for the lifetime of the
app via an ``AsyncExitStack``, and registers their tools into the shared tool registry.

Failures to connect are non-fatal: Kavi logs a notice and continues without that server.
"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from typing import Any

from kavi.config.schema import McpServerConfig
from kavi.mcp.tools import McpTool
from kavi.tools.registry import ToolRegistry

# Per-server connection budget. Discovered/third-party servers (e.g. Codex's
# node_repl) can be slow or unreachable; a bounded wait keeps startup snappy.
DEFAULT_CONNECT_TIMEOUT = 20.0


class McpManager:
    def __init__(
        self,
        servers: dict[str, McpServerConfig],
        registry: ToolRegistry,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
    ) -> None:
        self.servers = servers
        self.registry = registry
        self.connect_timeout = connect_timeout
        self._stack = AsyncExitStack()
        self._sessions: dict[str, Any] = {}
        self._server_tools: dict[str, list[str]] = {}
        self.notices: list[str] = []

    def connected_servers(self) -> dict[str, list[str]]:
        return dict(self._server_tools)

    async def connect_all(self) -> None:
        for name, cfg in self.servers.items():
            if not cfg.enabled:
                continue
            try:
                await asyncio.wait_for(self._connect_one(name, cfg), timeout=self.connect_timeout)
            except (asyncio.TimeoutError, TimeoutError):
                self.notices.append(
                    f"MCP server '{name}' timed out after {self.connect_timeout:.0f}s; skipped."
                )
            except Exception as exc:  # noqa: BLE001
                self.notices.append(f"MCP server '{name}' failed to connect: {exc}")

    async def _connect_one(self, name: str, cfg: McpServerConfig) -> None:
        from mcp import ClientSession

        if cfg.transport == "stdio":
            from mcp import StdioServerParameters
            from mcp.client.stdio import stdio_client

            if not cfg.command:
                raise ValueError("stdio transport requires a 'command'")
            params = StdioServerParameters(command=cfg.command, args=cfg.args, env=cfg.env or None)
            read, write = await self._stack.enter_async_context(stdio_client(params))
        elif cfg.transport == "sse":
            from mcp.client.sse import sse_client

            if not cfg.url:
                raise ValueError("sse transport requires a 'url'")
            read, write = await self._stack.enter_async_context(sse_client(cfg.url))
        else:  # http
            from mcp.client.streamable_http import streamablehttp_client

            if not cfg.url:
                raise ValueError("http transport requires a 'url'")
            read, write, _ = await self._stack.enter_async_context(streamablehttp_client(cfg.url))

        session = await self._stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        self._sessions[name] = session

        listed = await session.list_tools()
        tool_names: list[str] = []
        for tool in listed.tools:
            wrapper = McpTool(
                server_name=name,
                tool_name=tool.name,
                description=tool.description or "",
                input_schema=getattr(tool, "inputSchema", None) or {},
                call=self._make_caller(name),
            )
            self.registry.register(wrapper)
            tool_names.append(tool.name)
        self._server_tools[name] = tool_names

    def _make_caller(self, server_name: str):
        async def _call(tool_name: str, args: dict[str, Any]) -> str:
            session = self._sessions[server_name]
            result = await session.call_tool(tool_name, args)
            return _extract_text(result)

        return _call

    async def aclose(self) -> None:
        await self._stack.aclose()
        self._sessions.clear()


def _extract_text(result: Any) -> str:
    parts: list[str] = []
    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        if text is not None:
            parts.append(text)
        else:
            parts.append(str(item))
    return "\n".join(parts)
