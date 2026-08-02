"""Tool system base classes.

Each tool is self-contained: a Pydantic input model (schema + validation), a permission
profile, and an async ``run`` method. Tools return plain data (:class:`ToolResult`) and do
not import the UI, keeping execution decoupled from rendering.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel

from kavi.config.schema import KaviConfig, PermissionDecision
from kavi.messages import ToolSchema

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


@dataclass
class ToolContext:
    """Runtime services handed to a tool when it executes."""

    cwd: Path
    config: KaviConfig
    # Factory that runs a sub-agent prompt with a restricted toolset and returns its
    # final text. Provided by the engine; used by the Task tool.
    run_subagent: Callable[..., Awaitable[str]] | None = None
    # Auxiliary LLM helper for cheap side calls (WebFetch distillation, etc.).
    # ``(system, user) -> text``; returns "" if no provider is available.
    complete: Callable[[str, str], Awaitable[str]] | None = None
    # Stage extra content blocks (e.g. an image) to be attached as a user message
    # right after the current tool-call batch. Used by view_image for vision.
    stage_user_content: Callable[[Any], None] | None = None
    # Push partial output to the UI while a tool is still running (e.g. streaming
    # bash output line-by-line). ``(text) -> None``; may be None if the caller
    # doesn't support progress reporting.
    on_progress: Callable[[str], Awaitable[None]] | None = None
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    """Outcome of a tool execution."""

    content: str  # text returned to the model
    is_error: bool = False
    title: str | None = None  # short summary for the UI
    display: str | None = None  # richer body for the UI (falls back to content)
    # Structured payload for front-ends that render a tool specially (e.g. the
    # TodoWrite checklist). Ignored by text/headless front-ends.
    ui_payload: dict[str, Any] | None = None

    @classmethod
    def error(cls, message: str, title: str | None = None) -> ToolResult:
        return cls(content=message, is_error=True, title=title or "Error")


class Tool(abc.ABC):
    """Abstract base class for all tools."""

    name: ClassVar[str] = "tool"
    description: ClassVar[str] = ""
    InputModel: ClassVar[type[BaseModel]]

    # Read-only tools never mutate state and may run concurrently.
    is_read_only: ClassVar[bool] = False
    is_concurrency_safe: ClassVar[bool] = False

    def default_permission(self) -> PermissionDecision:
        """Baseline decision when no rule matches. Read-only tools auto-allow."""
        return "allow" if self.is_read_only else "ask"

    def classify_permission(self, data: BaseModel) -> PermissionDecision | None:  # noqa: ARG002
        """Optional per-call risk classification.

        Return ``"ask"`` to force an approval prompt for *this specific call*
        even when a broad allow rule would otherwise match (used by Bash to
        always confirm destructive commands like ``rm -rf``). Return ``None`` to
        defer entirely to the normal rule resolution.
        """
        return None

    def permission_subject(self, data: BaseModel) -> str:  # noqa: ARG002
        """String matched against permission rule patterns (path, command, etc.)."""
        return ""

    def to_schema(self) -> ToolSchema:
        schema = self.InputModel.model_json_schema()
        schema.pop("title", None)
        return ToolSchema(
            name=self.name, description=self.description.strip(), input_schema=schema
        )

    def render_call(self, data: BaseModel) -> str:
        """One-line human description of an invocation, for the UI."""
        return self.name

    @abc.abstractmethod
    async def run(self, data: BaseModel, ctx: ToolContext) -> ToolResult:
        """Execute the tool. ``data`` is a validated ``InputModel`` instance."""
        raise NotImplementedError

    # -- helpers -----------------------------------------------------------------

    def validate(self, raw: dict[str, Any]) -> BaseModel:
        return self.InputModel.model_validate(raw)

    @staticmethod
    def resolve_path(cwd: Path, path_str: str) -> Path:
        p = Path(path_str).expanduser()
        return p if p.is_absolute() else (cwd / p)
