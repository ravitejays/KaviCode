"""Tests for the TodoWrite and ViewImage tools."""

from __future__ import annotations

import base64
from pathlib import Path

from kavi.messages import ImageBlock
from kavi.tools.base import ToolContext
from kavi.tools.todo import TodoItem, TodoWriteInput, TodoWriteTool
from kavi.tools.viewimage import ViewImageInput, ViewImageTool

# 1x1 transparent PNG.
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


async def test_todo_write_renders_and_counts(tool_ctx: ToolContext):
    res = await TodoWriteTool().run(
        TodoWriteInput(
            todos=[
                TodoItem(content="step 1", status="completed"),
                TodoItem(content="step 2", status="in_progress"),
                TodoItem(content="step 3", status="pending"),
            ]
        ),
        tool_ctx,
    )
    assert not res.is_error
    assert "1/3 completed" in res.content
    assert "[x] step 1" in res.content
    assert "[~] step 2" in res.content
    assert tool_ctx.extras["todos"][0]["content"] == "step 1"


async def test_todo_calls_render_callback(cwd: Path, config):
    captured = {}
    ctx = ToolContext(cwd=cwd, config=config, extras={"render_todos": lambda t: captured.setdefault("t", t)})
    await TodoWriteTool().run(
        TodoWriteInput(todos=[TodoItem(content="x", status="pending")]), ctx
    )
    assert captured["t"][0]["content"] == "x"


async def test_view_image_stages_content(cwd: Path, config):
    (cwd / "pic.png").write_bytes(_PNG)
    staged: list = []
    ctx = ToolContext(cwd=cwd, config=config, stage_user_content=staged.append)
    res = await ViewImageTool().run(ViewImageInput(path="pic.png"), ctx)
    assert not res.is_error
    assert len(staged) == 1
    block = staged[0]
    assert isinstance(block, ImageBlock)
    assert block.media_type == "image/png"
    assert block.data_url().startswith("data:image/png;base64,")


async def test_view_image_missing_file(tool_ctx: ToolContext):
    res = await ViewImageTool().run(ViewImageInput(path="nope.png"), tool_ctx)
    assert res.is_error


async def test_view_image_rejects_non_image(cwd: Path, config):
    (cwd / "notimg.png").write_bytes(b"this is not an image")
    ctx = ToolContext(cwd=cwd, config=config, stage_user_content=lambda b: None)
    res = await ViewImageTool().run(ViewImageInput(path="notimg.png"), ctx)
    assert res.is_error


async def test_view_image_needs_session(cwd: Path, config):
    (cwd / "pic.png").write_bytes(_PNG)
    ctx = ToolContext(cwd=cwd, config=config, stage_user_content=None)
    res = await ViewImageTool().run(ViewImageInput(path="pic.png"), ctx)
    assert res.is_error
    assert "active agent session" in res.content
