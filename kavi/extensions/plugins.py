"""Plugins - bundles of commands / skills / hooks contributed as a unit.

A plugin is simply a directory under ``~/.kavi/plugins/<name>/`` that follows the
same layout as a source root (``commands/``, ``skills/``, ``hooks.json``). Because
:func:`kavi.extensions.sources.plugin_roots` already treats each plugin directory
as a search root, an installed plugin's extensions are discovered automatically.

This module just handles listing and installing/removing plugins.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from kavi.extensions import sources


@dataclass(frozen=True)
class Plugin:
    name: str
    path: Path

    def components(self) -> list[str]:
        """Which extension kinds this plugin ships (for display)."""
        kinds = []
        if (self.path / "commands").is_dir():
            kinds.append("commands")
        if (self.path / "skills").is_dir():
            kinds.append("skills")
        if (self.path / "hooks.json").is_file():
            kinds.append("hooks")
        return kinds


def list_plugins() -> list[Plugin]:
    pdir = sources.plugins_dir()
    if not pdir.is_dir():
        return []
    return [Plugin(name=p.name, path=p) for p in sorted(pdir.iterdir()) if p.is_dir()]


def install_plugin(source: str) -> Plugin:
    """Install a plugin from a local directory or a git URL. Returns the Plugin.

    Raises ValueError on failure.
    """
    pdir = sources.plugins_dir()
    pdir.mkdir(parents=True, exist_ok=True)

    src_path = Path(source).expanduser()
    if src_path.is_dir():
        name = src_path.name
        dest = pdir / name
        if dest.exists():
            raise ValueError(f"Plugin '{name}' is already installed.")
        shutil.copytree(src_path, dest)
        return Plugin(name=name, path=dest)

    # Treat as a git URL.
    if source.endswith(".git") or source.startswith(("http://", "https://", "git@")):
        name = source.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
        dest = pdir / name
        if dest.exists():
            raise ValueError(f"Plugin '{name}' is already installed.")
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", source, str(dest)],
                check=True,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError(f"git clone failed: {exc}") from exc
        return Plugin(name=name, path=dest)

    raise ValueError(f"Not a directory or git URL: {source}")


def remove_plugin(name: str) -> bool:
    """Remove an installed plugin. Returns True if it existed."""
    dest = sources.plugins_dir() / name
    if not dest.is_dir():
        return False
    shutil.rmtree(dest)
    return True
