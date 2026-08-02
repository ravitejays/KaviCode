"""Centralized logging configuration for Kavi.

All modules should use ``get_logger(__name__)`` instead of bare ``print()`` calls.
The root logger is configured once via :func:`setup_logging` (called from the CLI
entry point), so every module gets consistent formatting and level control.
"""

from __future__ import annotations

import logging
import sys


def setup_logging(
    level: str = "WARNING",
    log_file: str | None = None,
    *,
    verbose: bool = False,
    debug: bool = False,
) -> None:
    """Configure the ``kavi`` logger hierarchy.

    Called once at startup from :mod:`kavi.cli`. The *verbose* and *debug*
    keyword arguments are convenience shortcuts for ``level="INFO"`` and
    ``level="DEBUG"`` respectively.
    """
    if debug:
        effective = "DEBUG"
    elif verbose:
        effective = "INFO"
    else:
        effective = level.upper()

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))

    logging.basicConfig(
        level=getattr(logging, effective, logging.WARNING),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
        force=True,  # re-configure even if basicConfig was already called
    )

    # Keep noisy third-party loggers quiet unless we're in full debug mode.
    if effective != "DEBUG":
        for noisy in ("httpx", "httpcore", "openai", "anthropic", "mcp"):
            logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Return a logger under the ``kavi`` namespace.

    Usage::

        from kavi.log import get_logger
        logger = get_logger(__name__)
        logger.info("something happened")
    """
    # Strip the package prefix if already present to avoid ``kavi.kavi.tools.bash``.
    clean = name.removeprefix("kavi.") if name.startswith("kavi.") else name
    return logging.getLogger(f"kavi.{clean}")
