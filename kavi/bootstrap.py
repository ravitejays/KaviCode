"""First-run environment setup.

Keeps Kavi easy to install and run everywhere (Windows included):

* :func:`configure_runtime` makes the console UTF-8 and selects an asyncio event
  loop that supports subprocesses on Windows.
* :func:`ensure_dependencies` auto-installs any missing runtime dependency the
  first time Kavi is launched, so users never have to hunt down ``ddgs`` &co.

This module deliberately imports **only the standard library** so it can run
before third-party packages are guaranteed to be present.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

from kavi.log import get_logger

logger = get_logger(__name__)

# Import name -> human-friendly pip name (only differs where they diverge).
_REQUIRED: dict[str, str] = {
    "textual": "textual",
    "rich": "rich",
    "pydantic": "pydantic",
    "pydantic_settings": "pydantic-settings",
    "anthropic": "anthropic",
    "openai": "openai",
    "httpx": "httpx",
    "mcp": "mcp",
    "platformdirs": "platformdirs",
    "pathspec": "pathspec",
    "tomli_w": "tomli-w",
    "ddgs": "ddgs",
}


def _missing() -> list[str]:
    """Return the import names of any runtime dependency that is not installed."""
    return [mod for mod in _REQUIRED if importlib.util.find_spec(mod) is None]


def project_root() -> Path | None:
    """Locate the source checkout root (the folder holding ``pyproject.toml``)."""
    here = Path(__file__).resolve()
    for parent in (here.parent, *here.parents):
        if (parent / "pyproject.toml").exists():
            return parent
    return None


def _install_command(missing: list[str]) -> list[str]:
    """Build the pip command that best restores the missing dependencies."""
    root = project_root()
    base = [sys.executable, "-m", "pip", "install"]
    if root is not None and (root / "requirements.txt").exists():
        return [*base, "-r", str(root / "requirements.txt")]
    if root is not None:
        return [*base, str(root)]
    # Installed from a wheel with no source tree: install the packages by name.
    return [*base, *(_REQUIRED[m] for m in missing)]


def ensure_dependencies() -> None:
    """Install missing runtime dependencies on first launch.

    Disabled by setting ``KAVI_NO_BOOTSTRAP=1`` (e.g. in locked-down CI). Any
    failure prints a clear manual-install hint and exits rather than crashing
    with an opaque ``ImportError`` later.
    """
    if os.environ.get("KAVI_NO_BOOTSTRAP") == "1":
        return

    missing = _missing()
    if not missing:
        return

    pretty = ", ".join(sorted(missing))
    logger.info("First-run setup: installing missing dependencies (%s)...", pretty)
    try:
        subprocess.run(_install_command(missing), check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        _fail(pretty, exc)
        return

    importlib.invalidate_caches()
    still_missing = _missing()
    if still_missing:
        _fail(", ".join(sorted(still_missing)), None)


def _fail(pretty: str, exc: object) -> None:
    root = project_root()
    hint = "pip install kavi"
    if root is not None and (root / "requirements.txt").exists():
        hint = f'pip install -r "{root / "requirements.txt"}"'
    elif root is not None:
        hint = f'pip install "{root}"'
    reason = f" ({exc})" if exc else ""
    logger.error(
        "Could not install required dependencies: %s%s\n"
        "       Please install them manually and retry:\n"
        "         %s",
        pretty,
        reason,
        hint,
    )
    raise SystemExit(1)


def configure_runtime() -> None:
    """Make the runtime friendly on every platform (especially Windows)."""
    # Force UTF-8 output so box-drawing/emoji render on legacy Windows consoles.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass

    if sys.platform == "win32":
        # The Proactor loop is required for asyncio subprocesses (Bash, Grep,
        # hooks). It is the default on modern Python, but set it explicitly so a
        # library that switched to the Selector loop can't break shell tools.
        import asyncio

        policy = getattr(asyncio, "WindowsProactorEventLoopPolicy", None)
        if policy is not None:
            try:
                asyncio.set_event_loop_policy(policy())
            except Exception:  # noqa: BLE001 - never block startup on this
                pass
