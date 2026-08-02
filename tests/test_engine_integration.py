import asyncio
from pathlib import Path

import pytest

from kavi.agent.context import Conversation
from kavi.agent.engine import AgentCallbacks, AgentEngine
from kavi.config.schema import KaviConfig, PermissionConfig, Provider
from kavi.messages import ToolUseBlock, Usage
from kavi.permissions.engine import PermissionEngine
from kavi.tools.base import ToolResult
from kavi.tools.registry import build_builtin_registry
from tests.fakes import FakeProvider, text_turn, tool_turn


class TrackingCallbacks(AgentCallbacks):
    def __init__(self):
        self.progress_calls = []
        self.notices = []
        
    async def on_tool_progress(self, block: ToolUseBlock, output: str) -> None:
        self.progress_calls.append((block.name, output))
        
    async def on_notice(self, text: str) -> None:
        self.notices.append(text)


def _engine(provider, cwd, callbacks):
    config = KaviConfig(
        provider=Provider.ANTHROPIC,
        model="claude-sonnet-4-test",
        permissions=PermissionConfig(yolo=True), # auto allow tools
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


@pytest.mark.asyncio
async def test_engine_bash_background_integration(cwd: Path):
    import sys
    
    script = (
        "import sys, time\n"
        "print('started')\n"
        "sys.stdout.flush()\n"
    )
    script_path = cwd / "test_int_bg.py"
    script_path.write_text(script)
    
    # 1. Model asks to run a command in the background
    # 2. Command finishes in the background, engine flushes notification to conversation
    provider = FakeProvider(
        [
            tool_turn("t1", "Bash", {"command": f"{sys.executable} test_int_bg.py", "run_in_background": True}),
            # Next turn the model should see the notification and just say ok
            text_turn("looks good"),
        ]
    )
    cb = TrackingCallbacks()
    engine = _engine(provider, cwd, cb)
    
    # Run first turn (spawns background and finishes the agent run).
    await engine.run("start server")
    
    # Wait for the background task to complete and push to queue
    await asyncio.sleep(0.5)
    
    # Run a second turn to flush the queue into the conversation
    provider._turns.append(text_turn("ok"))
    await engine.run("status?")
    
    # Verify the notification was injected before the last user message
    messages = engine.conversation.messages
    assert any("Background command" in m.text() and "completed" in m.text() for m in messages if m.role == "user")
