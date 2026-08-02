"""Slash command framework.

Commands are invoked from the REPL as ``/name args``. A command may:
  - return informational ``text`` to display,
  - return a ``prompt`` to feed to the agent as a user turn (a "prompt command"),
  - request a state change (clear the conversation, quit, switch model).
"""

from __future__ import annotations

import abc
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from kavi.app import KaviApp


@dataclass
class SelectOption:
    """A single, selectable row in an interactive command menu."""

    id: str
    label: str
    description: str = ""


@dataclass
class Selection:
    """An interactive, arrow-navigable menu returned by a command.

    ``on_select`` is invoked with the chosen option id and returns a follow-up
    :class:`CommandOutput` (for example, a confirmation message after switching model).
    """

    title: str
    options: list[SelectOption]
    on_select: Callable[[KaviApp, str], Awaitable[CommandOutput]]
    filterable: bool = False


@dataclass
class CommandOutput:
    text: str | None = None
    prompt: str | None = None
    clear: bool = False
    quit: bool = False
    select: Selection | None = None


class Command(abc.ABC):
    name: ClassVar[str]
    description: ClassVar[str] = ""
    usage: ClassVar[str] = ""

    @abc.abstractmethod
    async def run(self, app: KaviApp, args: str) -> CommandOutput:
        raise NotImplementedError
