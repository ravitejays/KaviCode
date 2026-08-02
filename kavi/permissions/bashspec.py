"""Structured understanding of shell commands for the permission layer.

The permission engine needs to know how dangerous a ``Bash`` command is *before*
running it. Rather than one flat regex, this module classifies a command by
inspecting each pipeline/segment: it recognises well-known read-only programs,
known-destructive program/sub-command pairs, and a family of irreversible
patterns (``rm -rf``, ``git push --force``, ``mkfs``, fork bombs, Windows /
PowerShell equivalents, ...).

A command's overall risk is the worst risk among its segments (split on
``; && || |``), so ``ls && rm -rf build`` is correctly flagged destructive.

Zero dependencies, best-effort: a determined model can still obscure intent, so
this is a safety net that decides *whether to prompt*, not a sandbox.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Risk = Literal["read", "exec", "network", "destructive"]

READ: Risk = "read"
EXEC: Risk = "exec"
NETWORK: Risk = "network"
DESTRUCTIVE: Risk = "destructive"

_ORDER: dict[Risk, int] = {READ: 0, EXEC: 1, NETWORK: 2, DESTRUCTIVE: 3}

# Programs that only read/inspect state - safe to treat as non-mutating.
_READ_ONLY = {
    "ls", "cat", "pwd", "echo", "printf", "which", "type", "whoami", "id",
    "date", "env", "printenv", "head", "tail", "wc", "stat", "file", "df",
    "du", "uname", "hostname", "tree", "basename", "dirname", "realpath",
    "readlink", "sort", "uniq", "cut", "awk", "sed", "grep", "egrep", "fgrep",
    "rg", "find", "fd", "diff", "cmp", "less", "more", "man", "help", "true",
    "false", "test", "sleep", "seq", "yes", "tr", "column", "jq", "yq",
}

# Programs that reach the network (informational; surfaced for callers that want it).
_NETWORK = {"curl", "wget", "ssh", "scp", "rsync", "ping", "nc", "telnet", "ftp"}

# Sub-command-aware programs: a first arg here means "read-only inspection".
_READ_SUBCOMMANDS = {
    "git": {
        "status", "log", "diff", "show", "branch", "remote", "config",
        "rev-parse", "describe", "blame", "ls-files", "shortlog", "reflog",
        "whatchanged", "cat-file", "tag", "grep",
    },
    "docker": {"ps", "images", "logs", "inspect", "version", "info"},
    "kubectl": {"get", "describe", "logs", "version"},
    "npm": {"list", "ls", "view", "outdated"},
    "pip": {"list", "show", "freeze"},
}

# (program, first-arg) pairs that are irreversible / high-impact.
_DESTRUCTIVE_SUBCOMMANDS = {
    ("git", "push"), ("git", "reset"), ("git", "clean"),
    ("docker", "rm"), ("docker", "rmi"), ("docker", "prune"),
    ("kubectl", "delete"), ("helm", "delete"), ("helm", "uninstall"),
    ("terraform", "destroy"), ("terraform", "apply"),
    ("systemctl", "stop"), ("systemctl", "disable"),
    ("npm", "publish"), ("pip", "uninstall"), ("brew", "uninstall"),
    ("gh", "repo"),
}

# Irreversible / dangerous single patterns (cross-platform).
_DESTRUCTIVE_PATTERNS = [
    re.compile(r"\brm\s+(-[a-zA-Z]*\s+)*-?[a-zA-Z]*f", re.I),
    re.compile(r"\brm\s+-[a-zA-Z]*r", re.I),            # rm -r / -rf / -fr
    re.compile(r"\bfind\b.*-delete\b", re.I),
    re.compile(r"\bgit\s+(push|reset\s+--hard|clean\s+-[a-z]*f|checkout\s+--\s)", re.I),
    re.compile(r"\bgit\s+push\b.*(--force|-f)\b", re.I),
    re.compile(r"\b(mkfs|dd|shred|truncate)\b", re.I),
    re.compile(r"\bchmod\s+-R\b", re.I),
    re.compile(r"\bchown\s+-R\b", re.I),
    re.compile(r">\s*/dev/sd", re.I),
    re.compile(r"\bsudo\b", re.I),
    re.compile(r":\(\)\s*\{.*\};:", re.I),               # fork bomb
    # Windows / cmd.exe destructive equivalents.
    re.compile(r"\b(del|erase)\b.*/[a-z]*[sq]", re.I),   # del /s /q
    re.compile(r"\b(rd|rmdir)\b.*/s", re.I),             # rmdir /s
    re.compile(r"\bformat\b\s+[a-z]:", re.I),            # format C:
    re.compile(r"\bRemove-Item\b.*-Recurse", re.I),      # PowerShell rm -r
    re.compile(r"\b(Stop|Remove)-Computer\b", re.I),
]

# Split a command line into pipeline/list segments.
_SEGMENT_SPLIT = re.compile(r"\|\||&&|[;|&]")

_LABELS: dict[Risk, str] = {
    READ: "read-only",
    EXEC: "runs a command",
    NETWORK: "network access",
    DESTRUCTIVE: "potentially destructive",
}


@dataclass(frozen=True)
class Classification:
    risk: Risk
    label: str

    @property
    def destructive(self) -> bool:
        return self.risk == DESTRUCTIVE

    @property
    def read_only(self) -> bool:
        return self.risk == READ


def _program(segment: str) -> tuple[str, str]:
    """Return (program, first_arg) for a segment, stripping any path prefix."""
    tokens = segment.strip().split()
    if not tokens:
        return "", ""
    prog = tokens[0].rsplit("/", 1)[-1].rsplit("\\", 1)[-1].lower()
    arg = tokens[1].lower() if len(tokens) > 1 else ""
    return prog, arg


def _segment_risk(segment: str) -> Risk:
    if any(p.search(segment) for p in _DESTRUCTIVE_PATTERNS):
        return DESTRUCTIVE
    prog, arg = _program(segment)
    if not prog:
        return READ
    if (prog, arg) in _DESTRUCTIVE_SUBCOMMANDS:
        return DESTRUCTIVE
    if prog in _READ_SUBCOMMANDS:
        return READ if arg in _READ_SUBCOMMANDS[prog] else EXEC
    if prog in _NETWORK:
        return NETWORK
    if prog in _READ_ONLY:
        return READ
    return EXEC


def classify(command: str) -> Classification:
    """Classify a shell command by its worst-risk segment."""
    command = command or ""
    # Some destructive signatures (notably the fork bomb ``:(){ :|:& };:``) span
    # the delimiters we split on, so scan the whole command first.
    if any(p.search(command) for p in _DESTRUCTIVE_PATTERNS):
        return Classification(risk=DESTRUCTIVE, label=_LABELS[DESTRUCTIVE])
    worst: Risk = READ
    for segment in _SEGMENT_SPLIT.split(command):
        if not segment.strip():
            continue
        r = _segment_risk(segment)
        if _ORDER[r] > _ORDER[worst]:
            worst = r
    return Classification(risk=worst, label=_LABELS[worst])


def is_destructive(command: str) -> bool:
    """Convenience: True if any segment is irreversible / high-impact."""
    return classify(command).destructive
