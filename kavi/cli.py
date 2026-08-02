"""Command-line entrypoint for Kavi.

Heavy, third-party-backed modules are imported lazily (inside :func:`main`) so
that the first-run dependency bootstrap can run *before* anything that needs an
uninstalled package is imported.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

from kavi import CREATOR, __version__


def _build_parser(provider_ids: list[str]) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kavi",
        description=f"Kavi Code - a terminal AI coding agent. Created by {CREATOR}.",
    )
    parser.add_argument("prompt", nargs="?", help="Optional prompt (implies headless mode).")
    parser.add_argument("-p", "--print", dest="headless", metavar="PROMPT",
                        help="Run a single prompt headlessly and print the result.")
    parser.add_argument("--provider", choices=provider_ids,
                        metavar="PROVIDER",
                        help="LLM provider to use (%(choices)s).")
    parser.add_argument("--model", help="Model name to use.")
    parser.add_argument("--profile", help="Use-case profile (coding, research, data, devops, ...).")
    parser.add_argument("--mode", choices=["default", "auto", "plan", "bypass"],
                        help="Permission mode: default (prompt), auto (accept edits), "
                        "plan (read-only), bypass (accept all).")
    parser.add_argument("--resume", nargs="?", const="latest", metavar="SESSION_ID",
                        help="Resume a previous session (latest if no id given).")
    parser.add_argument("--cwd", help="Working directory for the agent.")
    parser.add_argument("--thinking", action="store_true", help="Enable extended thinking.")
    parser.add_argument("--yolo", action="store_true",
                        help="Auto-approve all tool calls (dangerous).")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging.")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging.")
    parser.add_argument("--version", action="version", version=f"Kavi Code {__version__}")
    return parser


def _overrides_from_args(args: argparse.Namespace) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    if args.provider:
        overrides["provider"] = args.provider
    if args.model:
        overrides["model"] = args.model
    if args.profile:
        overrides["profile"] = args.profile
    if args.thinking:
        overrides["thinking"] = True
    perms: dict[str, Any] = {}
    if args.mode:
        perms["mode"] = args.mode
        perms["yolo"] = args.mode == "bypass"
    if args.yolo:
        perms["mode"] = "bypass"
        perms["yolo"] = True
    if perms:
        overrides["permissions"] = perms
    return overrides


def main(argv: list[str] | None = None) -> int:
    # First-run setup: make the console/loop friendly on every OS and install
    # any missing runtime dependency (e.g. ddgs) before we import it.
    from kavi.bootstrap import configure_runtime, ensure_dependencies
    from kavi.log import get_logger, setup_logging

    configure_runtime()
    ensure_dependencies()

    from kavi.config.loader import load_config
    from kavi.providers.presets import PROVIDER_PRESETS
    from kavi.session.store import SessionStore

    args = _build_parser(sorted(PROVIDER_PRESETS.keys())).parse_args(argv)
    
    # Configure root logger early.
    setup_logging(verbose=args.verbose, debug=args.debug)
    logger = get_logger(__name__)
    cwd = Path(args.cwd).expanduser().resolve() if args.cwd else Path.cwd()
    config = load_config(cwd=cwd, overrides=_overrides_from_args(args))

    resume = None
    if args.resume:
        store = SessionStore()
        target = store.latest() if args.resume == "latest" else None
        session_id = target.id if target else args.resume
        resume = store.load(session_id)
        if resume is None:
            logger.error("No session found for '%s'.", args.resume)
            return 1

    headless_prompt = args.headless or (args.prompt if args.prompt else None)
    if headless_prompt:
        return asyncio.run(_run_headless(config, cwd, headless_prompt))

    from kavi.app import KaviApp

    app = KaviApp(config=config, cwd=cwd, resume=resume)
    app.run()
    return 0


async def _run_headless(config, cwd: Path, prompt: str) -> int:  # noqa: ANN001
    """Run a single prompt without the TUI, streaming to stdout."""
    from kavi.agent.context import Conversation
    from kavi.agent.engine import AgentCallbacks, AgentEngine
    from kavi.agent.profiles import get_profile
    from kavi.agent.prompts import build_system_prompt
    from kavi.config.loader import profiles_dir
    from kavi.memory.loader import load_memory
    from kavi.permissions.engine import PermissionEngine
    from kavi.providers.registry import build_provider
    from kavi.tools.registry import build_builtin_registry
    from kavi.tools.task import TaskTool

    class ConsoleCallbacks(AgentCallbacks):
        def __init__(self, allow_all: bool) -> None:
            self._allow_all = allow_all

        async def on_text_delta(self, text: str) -> None:
            sys.stdout.write(text)
            sys.stdout.flush()

        async def on_tool_start(self, block, render: str) -> None:  # noqa: ANN001
            print(f"\n[tool] {render}", file=sys.stderr)

        async def on_tool_result(self, block, result) -> None:  # noqa: ANN001
            tag = "error" if result.is_error else "ok"
            print(f"[tool:{tag}] {result.title or ''}", file=sys.stderr)

        async def on_notice(self, text: str) -> None:
            print(f"[notice] {text}", file=sys.stderr)

        async def request_permission(self, tool_name, subject, render):  # noqa: ANN001
            if self._allow_all:
                return "allow"
            print(f"[denied] {render} (use --yolo to allow in headless mode)", file=sys.stderr)
            return "deny"

    try:
        provider = build_provider(config)
    except Exception as exc:  # noqa: BLE001
        get_logger(__name__).error("Provider not ready: %s", exc)
        return 1

    memory = load_memory(cwd)
    profile = get_profile(config.profile, profiles_dir())

    from kavi.extensions.hooks import HookRunner
    from kavi.extensions.skills import load_skills

    skills = load_skills(cwd)
    hooks = HookRunner.load(cwd)
    skills_meta = [(s.name, s.description) for s in skills.values()] or None

    conversation = Conversation(
        system_prompt=build_system_prompt(
            cwd,
            memory=memory,
            suffix=config.system_prompt_suffix,
            profile_prompt=profile.prompt,
            skills=skills_meta,
            plan_mode=config.permissions.effective_mode() == "plan",
            provider=config.provider.value,
            model=config.resolved_model(),
        )
    )
    registry = build_builtin_registry()
    registry.register(TaskTool())
    tool_names = profile.allowed_tools  # None => all tools
    engine = AgentEngine(
        config=config,
        provider=provider,
        registry=registry,
        conversation=conversation,
        permissions=PermissionEngine(config.permissions),
        cwd=cwd,
        callbacks=ConsoleCallbacks(allow_all=config.permissions.effective_mode() == "bypass"),
        tool_names=tool_names,
        hooks=hooks,
        skills=skills,
    )
    try:
        await engine.run(prompt)
    except Exception as exc:  # noqa: BLE001 - surface provider/tool errors cleanly
        get_logger(__name__).error("Error: %s", exc)
        return 1
    print()  # trailing newline
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
