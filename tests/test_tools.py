"""Tests for the built-in tools."""

from __future__ import annotations

from pathlib import Path

from kavi.tools.base import ToolContext
from kavi.tools.bash import BashInput, BashTool
from kavi.tools.edit import EditInput, EditTool
from kavi.tools.glob import GlobInput, GlobTool
from kavi.tools.grep import GrepInput, GrepTool
from kavi.tools.ls import LSInput, LSTool
from kavi.tools.multiedit import EditOp, MultiEditInput, MultiEditTool
from kavi.tools.read import ReadInput, ReadTool
from kavi.tools.write import WriteInput, WriteTool


async def test_write_then_read(tool_ctx: ToolContext, cwd: Path):
    write = WriteTool()
    res = await write.run(WriteInput(file_path="hello.txt", content="line1\nline2"), tool_ctx)
    assert not res.is_error
    assert (cwd / "hello.txt").read_text() == "line1\nline2"

    read = ReadTool()
    res = await read.run(ReadInput(file_path="hello.txt"), tool_ctx)
    assert "line1" in res.content
    assert "     1\t" in res.content  # line-numbered output


async def test_read_missing(tool_ctx: ToolContext):
    res = await ReadTool().run(ReadInput(file_path="nope.txt"), tool_ctx)
    assert res.is_error


async def test_edit_unique_and_ambiguous(tool_ctx: ToolContext, cwd: Path):
    (cwd / "f.txt").write_text("foo bar foo")
    edit = EditTool()

    res = await edit.run(
        EditInput(file_path="f.txt", old_string="foo", new_string="baz"), tool_ctx
    )
    assert res.is_error  # not unique

    res = await edit.run(
        EditInput(file_path="f.txt", old_string="foo", new_string="baz", replace_all=True),
        tool_ctx,
    )
    assert not res.is_error
    assert (cwd / "f.txt").read_text() == "baz bar baz"


async def test_edit_generates_diff(tool_ctx: ToolContext, cwd: Path):
    (cwd / "f.txt").write_text("a\nb\nc\n")
    res = await EditTool().run(
        EditInput(file_path="f.txt", old_string="b", new_string="B"), tool_ctx
    )
    assert res.display and "-b" in res.display and "+B" in res.display


async def test_multiedit(tool_ctx: ToolContext, cwd: Path):
    (cwd / "m.txt").write_text("one two three")
    res = await MultiEditTool().run(
        MultiEditInput(
            file_path="m.txt",
            edits=[EditOp(old_string="one", new_string="1"), EditOp(old_string="three", new_string="3")],
        ),
        tool_ctx,
    )
    assert not res.is_error
    assert (cwd / "m.txt").read_text() == "1 two 3"


async def test_bash_echo(tool_ctx: ToolContext):
    res = await BashTool().run(BashInput(command="echo hello-kavi"), tool_ctx)
    assert not res.is_error
    assert "hello-kavi" in res.content


async def test_bash_nonzero_exit(tool_ctx: ToolContext):
    res = await BashTool().run(BashInput(command="exit 3"), tool_ctx)
    assert res.is_error
    assert "exit code: 3" in res.content


async def test_ls_and_glob(tool_ctx: ToolContext, cwd: Path):
    (cwd / "a.py").write_text("x=1")
    (cwd / "b.py").write_text("y=2")
    ls = await LSTool().run(LSInput(path="."), tool_ctx)
    assert "a.py" in ls.content

    gl = await GlobTool().run(GlobInput(pattern="*.py"), tool_ctx)
    assert "a.py" in gl.content and "b.py" in gl.content


async def test_grep_content(tool_ctx: ToolContext, cwd: Path):
    (cwd / "c.txt").write_text("alpha\nbeta\ngamma")
    res = await GrepTool().run(GrepInput(pattern="beta", path="."), tool_ctx)
    assert "beta" in res.content


def test_tool_schema_shape():
    schema = ReadTool().to_schema()
    assert schema.name == "Read"
    assert schema.input_schema["type"] == "object"
    assert "file_path" in schema.input_schema["properties"]
