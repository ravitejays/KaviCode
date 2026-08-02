"""Tests for the agent engine tool-call loop."""

from __future__ import annotations

from pathlib import Path

from kavi.agent.context import Conversation
from kavi.agent.engine import AgentCallbacks, AgentEngine
from kavi.config.schema import KaviConfig, PermissionConfig, Provider
from kavi.messages import ToolUseBlock, Usage
from kavi.permissions.engine import PermissionEngine
from kavi.tools.base import ToolResult
from kavi.tools.registry import build_builtin_registry
from tests.fakes import FakeProvider, FlakyProvider, text_turn, tool_turn


class RecordingCallbacks(AgentCallbacks):
    def __init__(self) -> None:
        self.text = ""
        self.tools: list[str] = []
        self.results: list[ToolResult] = []
        self.usage = Usage()

    async def on_text_delta(self, text: str) -> None:
        self.text += text

    async def on_tool_start(self, block: ToolUseBlock, render: str) -> None:
        self.tools.append(block.name)

    async def on_tool_result(self, block: ToolUseBlock, result: ToolResult) -> None:
        self.results.append(result)

    async def on_turn_usage(self, usage: Usage) -> None:
        self.usage = self.usage + usage


def _engine(provider, cwd, callbacks, yolo=True):
    config = KaviConfig(
        provider=Provider.ANTHROPIC,
        model="claude-sonnet-4-test",
        permissions=PermissionConfig(yolo=yolo),
    )
    return AgentEngine(
        config=config,
        provider=provider,
        registry=build_builtin_registry(),
        conversation=Conversation(system_prompt="sys"),
        permissions=PermissionEngine(config.permissions),
        cwd=cwd,
        callbacks=callbacks,
    )


def _mode_engine(provider, cwd, callbacks, mode):
    config = KaviConfig(
        provider=Provider.ANTHROPIC,
        model="claude-sonnet-4-test",
        permissions=PermissionConfig(mode=mode),
    )
    return AgentEngine(
        config=config,
        provider=provider,
        registry=build_builtin_registry(),
        conversation=Conversation(system_prompt="sys"),
        permissions=PermissionEngine(config.permissions),
        cwd=cwd,
        callbacks=callbacks,
    )


async def test_engine_runs_tool_then_finishes(cwd: Path):
    provider = FakeProvider(
        [
            tool_turn("t1", "Write", {"file_path": "out.txt", "content": "hi there"}),
            text_turn("Wrote the file."),
        ]
    )
    cb = RecordingCallbacks()
    engine = _engine(provider, cwd, cb)

    final = await engine.run("please write a file")

    assert (cwd / "out.txt").read_text() == "hi there"
    assert "Write" in cb.tools
    assert final == "Wrote the file."
    assert cb.usage.output_tokens > 0
    # user + assistant(tool) + tool_result + assistant(text)
    assert len(engine.conversation.messages) == 4


async def test_engine_denies_without_permission(cwd: Path):
    provider = FakeProvider(
        [
            tool_turn("t1", "Write", {"file_path": "nope.txt", "content": "x"}),
            text_turn("could not."),
        ]
    )
    cb = RecordingCallbacks()  # default request_permission -> deny
    engine = _engine(provider, cwd, cb, yolo=False)

    await engine.run("write it")

    assert not (cwd / "nope.txt").exists()
    assert cb.results and cb.results[0].is_error


async def test_engine_no_tools_single_turn(cwd: Path):
    provider = FakeProvider([text_turn("just talking")])
    cb = RecordingCallbacks()
    engine = _engine(provider, cwd, cb)
    final = await engine.run("hi")
    assert final == "just talking"
    assert cb.tools == []


async def test_auto_mode_accepts_edit_without_prompt(cwd: Path):
    # In auto mode a Write is auto-accepted even though request_permission
    # (the default) would deny it.
    provider = FakeProvider(
        [
            tool_turn("t1", "Write", {"file_path": "auto.txt", "content": "yes"}),
            text_turn("done"),
        ]
    )
    cb = RecordingCallbacks()
    engine = _mode_engine(provider, cwd, cb, mode="auto")
    await engine.run("write it")
    assert (cwd / "auto.txt").read_text() == "yes"


async def test_auto_mode_prompts_destructive_bash(cwd: Path):
    # A destructive command must still be denied by the default (deny) callback,
    # even in auto mode.
    provider = FakeProvider(
        [
            tool_turn("t1", "Bash", {"command": "rm -rf important"}),
            text_turn("blocked"),
        ]
    )
    cb = RecordingCallbacks()
    engine = _mode_engine(provider, cwd, cb, mode="auto")
    await engine.run("delete it")
    assert cb.results and cb.results[0].is_error


async def test_plan_mode_denies_writes(cwd: Path):
    provider = FakeProvider(
        [
            tool_turn("t1", "Write", {"file_path": "plan.txt", "content": "x"}),
            text_turn("here is a plan"),
        ]
    )
    cb = RecordingCallbacks()
    engine = _mode_engine(provider, cwd, cb, mode="plan")
    await engine.run("change it")
    assert not (cwd / "plan.txt").exists()
    assert cb.results and cb.results[0].is_error
    assert "plan mode" in cb.results[0].content.lower()


async def test_plan_mode_allows_reads(cwd: Path):
    (cwd / "src.txt").write_text("hello")
    provider = FakeProvider(
        [
            tool_turn("t1", "Read", {"file_path": "src.txt"}),
            text_turn("read it"),
        ]
    )
    cb = RecordingCallbacks()
    engine = _mode_engine(provider, cwd, cb, mode="plan")
    await engine.run("read it")
    assert cb.results and not cb.results[0].is_error


async def test_plan_mode_hides_mutating_tools(cwd: Path):
    provider = FakeProvider([text_turn("ok")])
    cb = RecordingCallbacks()
    engine = _mode_engine(provider, cwd, cb, mode="plan")
    exposed = set(engine._active_tool_names() or [])
    assert "Read" in exposed
    assert "Write" not in exposed
    assert "Edit" not in exposed
    assert "Bash" not in exposed


async def test_engine_retries_malformed_tool_call(cwd: Path):
    # Simulates a provider (e.g. Groq/Llama) rejecting the model's malformed
    # tool call the first time, then succeeding on regeneration.
    err = RuntimeError(
        "Error code: 400 - tool call validation failed: attempted to call tool "
        "'Glob,{\"path\":\"x\"}' which was not in request.tools"
    )
    provider = FlakyProvider(fail_times=1, error=err, turns=[text_turn("recovered")])
    cb = RecordingCallbacks()
    engine = _engine(provider, cwd, cb)

    final = await engine.run("analyse this code")

    assert final == "recovered"
    assert provider.calls == 2  # failed once, retried once


async def test_engine_gives_up_on_persistent_bad_generation(cwd: Path):
    from kavi.providers.base import ProviderError

    err = RuntimeError("tool call validation failed: not in request.tools")
    provider = FlakyProvider(fail_times=99, error=err, turns=[])
    cb = RecordingCallbacks()
    engine = _engine(provider, cwd, cb)

    raised = False
    try:
        await engine.run("go")
    except ProviderError:
        raised = True
    assert raised
    assert provider.calls == 4  # initial + 3 retries
