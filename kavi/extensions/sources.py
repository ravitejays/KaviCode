"""Discovery of extension search roots (the "where do things live" layer).

Kavi looks for extensions (commands, skills, hooks, MCP config, memory) in a
fixed priority order, and is **compatible with Claude Code's and Codex's
layouts** so an existing setup works unchanged:

    project   <workspace>/.kavi/      (native, highest priority)
              <workspace>/.claude/     (Claude Code-compatible)
    user      ~/.kavi/                (native)
              ~/.claude/               (Claude Code-compatible)
              ~/.codex/                (Codex-compatible: skills, etc.)
    plugins   ~/.kavi/plugins/<name>/   (Kavi-installed plugins)
              ~/.claude/plugins/<name>/ (Claude Code plugins)

Every other extension module asks this one for paths; it is the single owner of
the native-plus-third-party detection so the rest of the codebase stays
convention free.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from kavi.config.loader import user_config_dir

# Hook config filenames within a root.
HOOK_FILES = ("hooks.json",)


@dataclass(frozen=True)
class Root:
    """One search root with a human label and a scope (project|user|plugin)."""

    path: Path
    scope: str  # "project" | "user" | "plugin"
    label: str


def base_roots(workspace: Path) -> list[Root]:
    """The core (non-plugin) roots in priority order, whether or not they exist."""
    home = Path.home()
    return [
        Root(workspace / ".kavi", "project", ".kavi (project)"),
        Root(workspace / ".claude", "project", ".claude (project)"),
        Root(user_config_dir(), "user", ".kavi (user)"),
        Root(home / ".claude", "user", ".claude (user)"),
        Root(home / ".codex", "user", ".codex (user)"),
    ]


def plugins_dir() -> Path:
    """Native plugin install directory (also the target for `/plugins install`)."""
    return user_config_dir() / "plugins"


def plugin_base_dirs() -> list[Path]:
    """Directories that hold plugin folders, native first then Claude Code."""
    return [plugins_dir(), Path.home() / ".claude" / "plugins"]


def plugin_roots() -> list[Root]:
    """Roots contributed by installed plugins (each plugin dir is a root).

    Scans both Kavi's own plugin dir and Claude Code's, so plugins installed for
    either tool contribute their skills/commands/hooks/MCP config.
    """
    out: list[Root] = []
    seen: set[Path] = set()
    for base in plugin_base_dirs():
        if not base.is_dir():
            continue
        for p in sorted(base.iterdir()):
            rp = p.resolve()
            if p.is_dir() and rp not in seen:
                seen.add(rp)
                out.append(Root(p, "plugin", f"plugin:{p.name}"))
    return out


def all_roots(workspace: Path, include_plugins: bool = True) -> list[Root]:
    roots = base_roots(workspace)
    if include_plugins:
        roots += plugin_roots()
    return roots


def dirs(workspace: Path, subdir: str, include_plugins: bool = True) -> list[Path]:
    """Existing ``<root>/<subdir>`` directories across all roots (priority order)."""
    out: list[Path] = []
    for r in all_roots(workspace, include_plugins):
        d = r.path / subdir
        if d.is_dir():
            out.append(d)
    return out


def config_files(
    workspace: Path, names: tuple[str, ...], include_plugins: bool = True
) -> list[Path]:
    """Existing config files named in ``names``, across roots + the workspace root."""
    out: list[Path] = []
    seen: set[Path] = set()

    def add(p: Path) -> None:
        rp = p.resolve()
        if p.is_file() and rp not in seen:
            seen.add(rp)
            out.append(p)

    for r in all_roots(workspace, include_plugins):
        for name in names:
            add(r.path / name)
    for name in names:  # bare workspace-level file (Claude Code convention)
        add(workspace / name)
    return out


def hook_config_files(workspace: Path) -> list[Path]:
    return config_files(workspace, HOOK_FILES)
