"""Command registry and dispatcher."""

from __future__ import annotations

from typing import TYPE_CHECKING

from kavi.commands.base import Command, CommandOutput

if TYPE_CHECKING:
    from kavi.app import KaviApp


class CommandRegistry:
    def __init__(self) -> None:
        self._commands: dict[str, Command] = {}

    def register(self, command: Command) -> None:
        self._commands[command.name] = command

    def get(self, name: str) -> Command | None:
        return self._commands.get(name)

    def all(self) -> list[Command]:
        return sorted(self._commands.values(), key=lambda c: c.name)

    def is_command(self, text: str) -> bool:
        return text.lstrip().startswith("/")

    async def dispatch(self, app: KaviApp, text: str) -> CommandOutput:
        stripped = text.lstrip()[1:]  # drop leading '/'
        name, _, args = stripped.partition(" ")
        command = self.get(name.strip())
        if command is None:
            return CommandOutput(text=f"Unknown command: /{name}. Type /help for a list.")
        return await command.run(app, args.strip())


def build_command_registry() -> CommandRegistry:
    from kavi.commands.builtins import ALL_COMMANDS

    registry = CommandRegistry()
    for command_cls in ALL_COMMANDS:
        registry.register(command_cls())
    return registry
