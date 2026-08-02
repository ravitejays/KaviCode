"""Frontmatter parsing and prompt-template expansion.

Two small, dependency-free utilities shared by the commands / skills loaders:

* ``parse`` - split a Markdown file into a metadata dict (from a leading ``---``
  YAML-ish frontmatter block) and the remaining body. Only the flat
  ``key: value`` / inline-list subset these files use is supported, so PyYAML is
  not required.

* ``expand_template`` - apply Claude-Code-style substitutions to a command body:
  ``$ARGUMENTS`` / ``$1``..``$9`` (positional args), ``@path`` (inline a file's
  contents) and ``!`cmd``` (inline a shell command's output).
"""

from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path
from typing import Any


def parse(text: str) -> tuple[dict[str, Any], str]:
    """Return (metadata, body). Metadata comes from a leading --- ... --- block."""
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return {}, text
    meta = _parse_block(lines[1:end])
    body = "\n".join(lines[end + 1 :]).lstrip("\n")
    return meta, body


def _parse_block(lines: list[str]) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    for raw in lines:
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        if not key:
            continue
        meta[key] = _coerce(value.strip())
    return meta


def _coerce(value: str) -> Any:
    if value == "":
        return ""
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_unquote(v.strip()) for v in inner.split(",") if v.strip()]
    low = value.lower()
    if low in ("true", "false"):
        return low == "true"
    return _unquote(value)


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def as_list(value: Any) -> list[str]:
    """Normalise a frontmatter value to a list of strings (accepts CSV strings)."""
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [v.strip() for v in str(value).split(",") if v.strip()]


# --------------------------------------------------------------- template expansion
_FILE_RE = re.compile(r"(?<!\w)@([^\s`'\"]+)")
_BANG_RE = re.compile(r"!`([^`]+)`")
_BANG_TIMEOUT = 30
_FILE_MAX = 40_000


def expand_template(
    body: str,
    args: str = "",
    workspace: Path | None = None,
    run_bash: bool = True,
) -> str:
    """Expand $ARGUMENTS / $N, then @file includes, then !`cmd` blocks."""
    text = _expand_args(body, args)
    if workspace is not None:
        text = expand_mentions(text, workspace)
    if run_bash:
        text = _expand_bang(text, workspace)
    return text


def _expand_args(body: str, args: str) -> str:
    parts = shlex.split(args) if args else []
    out = body.replace("$ARGUMENTS", args)
    for i in range(1, 10):
        out = out.replace(f"${i}", parts[i - 1] if i - 1 < len(parts) else "")
    return out


def expand_mentions(text: str, workspace: Path) -> str:
    """Inline the contents of ``@path`` mentions that resolve to readable files."""

    def repl(m: re.Match) -> str:
        rel = m.group(1)
        path = Path(rel)
        if not path.is_absolute():
            path = workspace / rel
        try:
            if not path.is_file():
                return m.group(0)
            data = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return m.group(0)
        if len(data) > _FILE_MAX:
            data = data[:_FILE_MAX] + "\n... (truncated)"
        return f"\n\n```{rel}\n{data}\n```\n"

    return _FILE_RE.sub(repl, text)


def _expand_bang(text: str, workspace: Path | None) -> str:
    def repl(m: re.Match) -> str:
        cmd = m.group(1)
        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                cwd=str(workspace) if workspace else None,
                capture_output=True,
                text=True,
                timeout=_BANG_TIMEOUT,
            )
            out = (proc.stdout or "") + (proc.stderr or "")
        except (OSError, subprocess.SubprocessError) as exc:
            out = f"(command failed: {exc})"
        return out.strip()

    return _BANG_RE.sub(repl, text)
