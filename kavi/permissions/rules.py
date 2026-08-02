"""Parsing and matching of permission rule strings.

A rule looks like ``ToolName`` or ``ToolName(pattern)``. The pattern is matched with
shell-style globbing against the tool's "permission subject" (for example a bash command
or a file path).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from fnmatch import fnmatch

_RULE_RE = re.compile(r"^\s*(?P<tool>[A-Za-z0-9_]+)\s*(?:\((?P<pattern>.*)\))?\s*$")


@dataclass(frozen=True)
class Rule:
    tool: str
    pattern: str | None = None

    def matches(self, tool_name: str, subject: str) -> bool:
        if self.tool != tool_name:
            return False
        if self.pattern is None or self.pattern == "":
            return True
        subject = subject or ""
        # Match on the full subject, and also treat the pattern as a prefix helper:
        # "Bash(git*)" matches "git status", "git commit -m ...", etc.
        return fnmatch(subject, self.pattern) or subject.startswith(self.pattern.rstrip("*"))


def parse_rule(raw: str) -> Rule | None:
    m = _RULE_RE.match(raw)
    if not m:
        return None
    return Rule(tool=m.group("tool"), pattern=m.group("pattern"))


def parse_rules(raws: list[str]) -> list[Rule]:
    rules = [parse_rule(r) for r in raws]
    return [r for r in rules if r is not None]
