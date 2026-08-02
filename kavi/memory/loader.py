"""Project memory - discover and load the KAVI.md hierarchy.

Memory files let a project (and the user) give Kavi persistent instructions. Discovery,
from lowest to highest precedence:
  1. User memory:    ~/.kavi/KAVI.md
  2. Ancestor dirs:  KAVI.md files from the repo root down to the cwd
  3. Current dir:    ./KAVI.md
Later files appear later in the combined prompt so they can refine earlier guidance.
"""

from __future__ import annotations

from pathlib import Path

from kavi.config.loader import user_config_dir

MEMORY_FILENAME = "KAVI.md"
MAX_MEMORY_CHARS = 40_000


def _read(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
        return text or None
    except (OSError, UnicodeDecodeError):
        return None


def discover_memory_files(cwd: Path) -> list[Path]:
    files: list[Path] = []

    user_file = user_config_dir() / MEMORY_FILENAME
    if user_file.is_file():
        files.append(user_file)

    # Ancestors from top-most down to cwd (so cwd wins).
    chain = [cwd, *cwd.parents]
    for directory in reversed(chain):
        candidate = directory / MEMORY_FILENAME
        if candidate.is_file() and candidate not in files:
            files.append(candidate)

    return files


def load_memory(cwd: Path) -> str | None:
    sections: list[str] = []
    total = 0
    for path in discover_memory_files(cwd):
        content = _read(path)
        if not content:
            continue
        block = f"<!-- {path} -->\n{content}"
        total += len(block)
        if total > MAX_MEMORY_CHARS:
            block = block[: max(0, MAX_MEMORY_CHARS - (total - len(block)))]
            sections.append(block)
            break
        sections.append(block)
    if not sections:
        return None
    return "\n\n".join(sections)


DEFAULT_TEMPLATE = """\
# KAVI.md

Project-specific instructions for the Kavi coding agent (by Bahumukh AI).

## Overview
Describe what this project is and its high-level architecture.

## Commands
- Build: `...`
- Test: `...`
- Lint: `...`

## Conventions
- Coding style, naming, and patterns Kavi should follow.

## Do / Don't
- Things Kavi should always or never do in this repo.
"""


def init_project_memory(cwd: Path) -> Path:
    """Create a starter KAVI.md in cwd if one does not exist. Returns the path."""
    path = cwd / MEMORY_FILENAME
    if not path.exists():
        path.write_text(DEFAULT_TEMPLATE, encoding="utf-8")
    return path
