"""Configuration schema for Kavi.

Uses Pydantic v2 models. Config is layered: built-in defaults are overridden by the
user config (``~/.kavi/config.toml``), then by a project config (``.kavi.toml``), then
by environment variables and CLI flags.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from kavi.providers.presets import PROVIDER_PRESETS

PermissionDecision = Literal["allow", "deny", "ask"]

# Session-wide permission posture. Controls how much the agent may do without
# stopping to ask:
#   default : read-only auto-allowed; writes/exec prompt; destructive always prompts.
#   auto    : read-only + non-destructive writes auto-allowed; destructive prompts.
#             (the "accept edits" mode)
#   plan    : strictly read-only; every write/exec tool is denied.
#   bypass  : everything auto-allowed, no prompts (same as --yolo; use with care).
PermissionMode = Literal["default", "auto", "plan", "bypass"]

PERMISSION_MODES: tuple[PermissionMode, ...] = ("default", "auto", "plan", "bypass")

PERMISSION_MODE_LABELS: dict[PermissionMode, str] = {
    "default": "prompt before edits and commands",
    "auto": "auto-accept edits; prompt for destructive actions",
    "plan": "read-only; no edits or commands",
    "bypass": "auto-accept everything (dangerous)",
}


class Provider(StrEnum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    OLLAMA = "ollama"
    GROQ = "groq"
    OPENROUTER = "openrouter"
    TOGETHER = "together"
    DEEPSEEK = "deepseek"
    XAI = "xai"
    FIREWORKS = "fireworks"
    CEREBRAS = "cerebras"
    MISTRAL = "mistral"
    PERPLEXITY = "perplexity"
    MOONSHOT = "moonshot"
    ZAI = "zai"
    NVIDIA = "nvidia"
    WORKERSAI = "workersai"
    GENAI_RPC = "genai-rpc"
    SARVAM = "sarvam"


# Sensible default model per provider, derived from the preset table.
DEFAULT_MODELS: dict[Provider, str] = {
    Provider(pid): preset.default_model
    for pid, preset in PROVIDER_PRESETS.items()
    if pid in Provider._value2member_map_
}


class McpServerConfig(BaseModel):
    """Definition of a single MCP server to connect to."""

    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str | None = None  # for HTTP/SSE transports
    transport: Literal["stdio", "sse", "http"] = "stdio"
    enabled: bool = True


class PermissionConfig(BaseModel):
    """Per-tool permission decisions plus fine-grained rules.

    ``tools`` maps a tool name to a default decision. ``allow`` / ``deny`` hold
    argument-pattern rules of the form ``"ToolName(pattern)"`` (for example
    ``"Bash(git*)"`` or ``"Write(src/**)"``).
    """

    default: PermissionDecision = "ask"
    tools: dict[str, PermissionDecision] = Field(default_factory=dict)
    allow: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)
    # Session-wide posture (see PermissionMode). Overlays the rules above.
    mode: PermissionMode = "default"
    # When true, no permission prompts are shown and everything is allowed.
    # Equivalent to mode="bypass"; kept for the --yolo flag / back-compat.
    yolo: bool = False

    def effective_mode(self) -> PermissionMode:
        """Resolve the active mode, honouring the legacy ``yolo`` flag."""
        return "bypass" if self.yolo else self.mode


class ProviderCredentials(BaseModel):
    """Per-provider connection details (keys usually come from env)."""

    api_key: str | None = None
    base_url: str | None = None
    # Optional OpenAI-compatible extras (used by gateways like genai-rpc).
    organization: str | None = None
    default_headers: dict[str, str] = Field(default_factory=dict)


class KaviConfig(BaseModel):
    """Top-level, fully-resolved runtime configuration."""

    provider: Provider = Provider.ANTHROPIC
    model: str | None = None  # resolved to DEFAULT_MODELS[provider] if None
    theme: str | None = None  # Textual theme name (e.g. "textual-dark", "textual-light")
    max_tokens: int = 8192
    temperature: float = 1.0
    thinking: bool = False
    thinking_budget_tokens: int = 4096

    # Number of tool-call loop iterations before the engine forcibly stops.
    # Set to 0 (or negative) for effectively unlimited steps.
    max_turns: int = 100

    # Active use-case profile (system-prompt suffix + optional tool allowlist).
    profile: str = "coding"

    permissions: PermissionConfig = Field(default_factory=PermissionConfig)
    credentials: dict[str, ProviderCredentials] = Field(default_factory=dict)
    mcp_servers: dict[str, McpServerConfig] = Field(default_factory=dict)

    # Auto-import MCP servers configured for Claude Code (.mcp.json / ~/.claude.json)
    # and Codex (~/.codex/config.toml). Native `mcp_servers` above always win on
    # name conflicts. Set to false to only use Kavi's own MCP config.
    import_external_mcp: bool = True

    # Extra system-prompt text appended after memory files.
    system_prompt_suffix: str | None = None

    # A smaller/faster model used for cheap auxiliary calls (context compaction
    # summaries and WebFetch page distillation). Falls back to the main model.
    small_fast_model: str | None = None

    # Run fast syntax/lint checks after every edit/write and feed any errors back
    # to the model so it can self-correct on the next step.
    post_edit_diagnostics: bool = True

    # Hard limit on session cost (in USD). Raises CostLimitExceeded if reached.
    max_cost_usd: float | None = None

    # Threshold for session cost (in USD) to emit a warning.
    warn_cost_usd: float | None = None

    def resolved_model(self) -> str:
        return self.model or DEFAULT_MODELS[self.provider]

    def resolved_small_fast_model(self) -> str:
        """Model to use for cheap auxiliary calls; falls back to the main model."""
        return self.small_fast_model or self.resolved_model()

    def creds_for(self, provider: Provider) -> ProviderCredentials:
        return self.credentials.get(provider.value, ProviderCredentials())
