"""Import MCP server definitions from other agent tools (Claude Code, Codex).

Kavi natively stores MCP servers in its own ``config.toml``. Many users, though,
already have servers configured for Claude Code or Codex. Rather than make them
re-declare everything, Kavi discovers those definitions so they show up here
automatically:

    Claude Code   <workspace>/.mcp.json            (project, `mcpServers` key)
                  ~/.claude.json                   (global + per-project `mcpServers`)
    Codex         ~/.codex/config.toml             (`[mcp_servers.*]` tables)

Native Kavi config always wins on name conflicts (see the caller in ``app.py``).
"""

from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path

from kavi.config.schema import McpServerConfig
from kavi.extensions import sources

_HTTP_TYPES = {"http", "streamable-http", "streamable_http", "streamablehttp"}


def _remote_transport(raw: dict) -> str:
    typ = str(raw.get("type") or raw.get("transport") or "").lower()
    return "http" if typ in _HTTP_TYPES else "sse"


def _from_claude_entry(raw: object) -> McpServerConfig | None:
    """Parse one Claude Code `mcpServers` entry into an McpServerConfig."""
    if not isinstance(raw, dict):
        return None
    url = raw.get("url")
    if url:
        return McpServerConfig(url=str(url), transport=_remote_transport(raw))
    command = raw.get("command")
    if not command:
        return None
    env = raw.get("env") or {}
    return McpServerConfig(
        command=str(command),
        args=[str(a) for a in (raw.get("args") or [])],
        env={str(k): str(v) for k, v in env.items()},
        transport="stdio",
    )


def _from_codex_entry(raw: object) -> McpServerConfig | None:
    """Parse one Codex `[mcp_servers.*]` table into an McpServerConfig.

    Codex supports both an inline ``env`` table and an ``env_vars`` list naming
    variables to inherit from the current environment.
    """
    if not isinstance(raw, dict):
        return None
    url = raw.get("url")
    if url:
        return McpServerConfig(url=str(url), transport=_remote_transport(raw))
    command = raw.get("command")
    if not command:
        return None
    env = {str(k): str(v) for k, v in (raw.get("env") or {}).items()}
    for name in raw.get("env_vars") or []:
        value = os.environ.get(str(name))
        if value is not None:
            env[str(name)] = value
    return McpServerConfig(
        command=str(command),
        args=[str(a) for a in (raw.get("args") or [])],
        env=env,
        transport="stdio",
    )


def discover_external_mcp(
    workspace: Path,
) -> tuple[dict[str, McpServerConfig], dict[str, str]]:
    """Discover MCP servers defined by Claude Code and Codex.

    Returns ``(servers, origins)`` where ``origins`` maps each server name to a
    short human label describing where it came from. The first definition of a
    given name wins.
    """
    servers: dict[str, McpServerConfig] = {}
    origins: dict[str, str] = {}

    def add(name: object, cfg: McpServerConfig | None, origin: str) -> None:
        key = str(name).strip()
        if not key or cfg is None or key in servers:
            return
        servers[key] = cfg
        origins[key] = origin

    # 1) Claude Code project/root .mcp.json files.
    for path in sources.config_files(workspace, (".mcp.json",)):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for name, raw in (data.get("mcpServers") or {}).items():
            add(name, _from_claude_entry(raw), "claude (.mcp.json)")

    # 2) Claude Code ~/.claude.json - global and project-scoped servers.
    claude_json = Path.home() / ".claude.json"
    if claude_json.is_file():
        try:
            data = json.loads(claude_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        for name, raw in (data.get("mcpServers") or {}).items():
            add(name, _from_claude_entry(raw), "claude (~/.claude.json)")
        project = (data.get("projects") or {}).get(str(workspace)) or {}
        for name, raw in (project.get("mcpServers") or {}).items():
            add(name, _from_claude_entry(raw), "claude (project)")

    # 3) Codex ~/.codex/config.toml.
    codex_cfg = Path.home() / ".codex" / "config.toml"
    if codex_cfg.is_file():
        try:
            data = tomllib.loads(codex_cfg.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            data = {}
        for name, raw in (data.get("mcp_servers") or {}).items():
            add(name, _from_codex_entry(raw), "codex (~/.codex/config.toml)")

    return servers, origins
