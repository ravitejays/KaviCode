"""Use-case profiles - the "moldable" layer of Kavi.

A profile bundles a system-prompt suffix and (optionally) a restricted tool set,
turning the same agent core into a different specialist: a coding agent, a
research analyst, a data/SQL agent, a DevOps operator, etc.

Ships with ``coding`` (the default), ``research``, ``data`` and ``devops``.
Users can add their own by dropping a ``.md`` (whole file becomes the prompt) or
``.toml`` (with ``description`` / ``prompt`` / ``allowed_tools``) file into
``~/.kavi/profiles/``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore


@dataclass(frozen=True)
class Profile:
    name: str
    description: str
    # Appended to the base system prompt as a specialization directive.
    prompt: str
    # None => all tools. Otherwise restrict to this tool-name allowlist.
    allowed_tools: list[str] | None = field(default=None)


_CODING = Profile(
    name="coding",
    description="General software engineering: read, edit, run, debug code.",
    prompt=(
        "You specialize in full-stack software engineering and building robust applications. "
        "Explore the codebase with Glob/Grep, read before editing, and write COMPLETE, PRODUCTION-READY "
        "implementations with ZERO placeholders, TODO stubs, or dummy logic. "
        "Always install required dependencies (via `npm install`, `pip install`, etc.) using Bash when adding imports. "
        "CRITICAL: You MUST use the TodoWrite tool to create a checklist before starting a task and keep it updated. "
        "You MUST verify your work by running local dev servers or tests in the background, checking the logs, "
        "and ensuring the app runs cleanly without errors before declaring the task complete."
    ),
)

_RESEARCH = Profile(
    name="research",
    description="Read-only investigation & analysis of a codebase or docs.",
    prompt=(
        "You specialize in investigation and explanation. Gather evidence with Read, "
        "Grep, Glob and LS, then synthesize clear, well-structured findings with "
        "concrete file:line references. Do not modify anything."
    ),
    allowed_tools=[
        "Read", "LS", "Glob", "Grep", "TodoWrite",
        "WebSearch", "WebFetch", "ViewImage", "Task",
    ],
)

_DATA = Profile(
    name="data",
    description="Data & SQL workflows: scripts, queries, analysis via Bash.",
    prompt=(
        "You specialize in data engineering and analysis. Write and run Python/SQL "
        "scripts via Bash, inspect outputs, and iterate. Show intermediate results and "
        "explain transformations. Keep scratch work in clearly named files."
    ),
)

_DEVOPS = Profile(
    name="devops",
    description="Ops/automation: shell-first system and infrastructure tasks.",
    prompt=(
        "You specialize in operations and automation. Favor robust, idempotent shell "
        "commands. Be cautious with destructive operations and explain the risk before "
        "running them. Confirm system state with read-only checks before and after."
    ),
)

BUILTIN_PROFILES: dict[str, Profile] = {
    p.name: p for p in (_CODING, _RESEARCH, _DATA, _DEVOPS)
}

DEFAULT_PROFILE = "coding"


def load_custom_profiles(profiles_dir: Path) -> dict[str, Profile]:
    """Load user-defined profiles from ``<profiles_dir>/*.{md,toml}``."""
    out: dict[str, Profile] = {}
    if not profiles_dir.is_dir():
        return out
    for path in sorted(profiles_dir.iterdir()):
        try:
            if path.suffix == ".md":
                out[path.stem] = Profile(
                    name=path.stem,
                    description=f"Custom profile ({path.name})",
                    prompt=path.read_text(encoding="utf-8"),
                )
            elif path.suffix == ".toml":
                data = tomllib.loads(path.read_text(encoding="utf-8"))
                name = data.get("name", path.stem)
                out[name] = Profile(
                    name=name,
                    description=data.get("description", "Custom profile"),
                    prompt=data.get("prompt", ""),
                    allowed_tools=data.get("allowed_tools"),
                )
        except Exception:  # noqa: BLE001 - a bad profile file must not crash startup
            continue
    return out


def all_profiles(profiles_dir: Path | None = None) -> dict[str, Profile]:
    """Built-in profiles overlaid with any custom ones (custom wins on name clash)."""
    merged = dict(BUILTIN_PROFILES)
    if profiles_dir is not None:
        merged.update(load_custom_profiles(profiles_dir))
    return merged


def get_profile(name: str, profiles_dir: Path | None = None) -> Profile:
    return all_profiles(profiles_dir).get(name, BUILTIN_PROFILES[DEFAULT_PROFILE])
