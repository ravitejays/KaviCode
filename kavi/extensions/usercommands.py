"""Custom slash commands loaded from Markdown files.

A file ``commands/foo.md`` becomes ``/foo``; ``commands/git/commit.md`` becomes
``/git:commit``. The file body is a prompt template (see
:mod:`kavi.extensions.frontmatter` for ``$ARGUMENTS`` / ``@file`` / ``!`cmd```
substitutions) that, when invoked, is sent to the agent as a normal turn.

Optional frontmatter:

    ---
    description: Short text shown in the slash menu
    argument-hint: <file>
    ---

Discovered from every source root (project, user, plugins), so dropping a file in
``~/.kavi/commands/`` or ``<repo>/.kavi/commands/`` is enough.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from kavi.commands.base import Command, CommandOutput
from kavi.extensions import frontmatter as fm
from kavi.extensions import sources

if TYPE_CHECKING:
    from kavi.app import KaviApp


class UserCommand(Command):
    """A slash command whose body expands into a prompt sent to the agent."""

    def __init__(self, name: str, description: str, body: str) -> None:
        self.name = name
        self.description = description
        self._body = body

    async def run(self, app: KaviApp, args: str) -> CommandOutput:  # noqa: D102
        prompt = fm.expand_template(self._body, args, workspace=app.cwd)
        return CommandOutput(prompt=prompt)


def _command_name(path: Path, base: Path) -> str:
    rel = path.relative_to(base).with_suffix("")
    return ":".join(rel.parts).lower()


def load_user_commands(workspace: Path, reserved: set[str]) -> list[UserCommand]:
    """Discover custom command files. Higher-priority roots and built-ins win.

    ``reserved`` is the set of built-in command names that must not be shadowed.
    """
    commands: list[UserCommand] = []
    seen: set[str] = set()
    for base in sources.dirs(workspace, "commands"):
        for path in sorted(base.rglob("*.md")):
            name = _command_name(path, base)
            if name in seen or name in reserved:
                continue
            seen.add(name)
            try:
                meta, body = fm.parse(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            commands.append(
                UserCommand(
                    name=name,
                    description=str(meta.get("description") or f"Custom command ({path.name})"),
                    body=body,
                )
            )
    return commands
