"""Tests for post-edit diagnostics and their integration with file tools."""

from __future__ import annotations

from pathlib import Path

from kavi.tools.base import ToolContext
from kavi.tools.diagnostics import check, format_issues
from kavi.tools.edit import EditInput, EditTool
from kavi.tools.write import WriteInput, WriteTool


async def test_check_valid_python(cwd: Path):
    p = cwd / "ok.py"
    p.write_text("x = 1\n")
    assert await check(p) == []


async def test_check_python_syntax_error(cwd: Path):
    p = cwd / "bad.py"
    p.write_text("def broken(:\n    pass\n")
    issues = await check(p)
    assert issues and "SyntaxError" in issues[0]


async def test_check_invalid_json(cwd: Path):
    p = cwd / "bad.json"
    p.write_text('{"a": 1,,}')
    issues = await check(p)
    assert issues and "JSON parse error" in issues[0]


async def test_check_valid_json(cwd: Path):
    p = cwd / "ok.json"
    p.write_text('{"a": 1}')
    assert await check(p) == []


async def test_check_unsupported_extension_is_clean(cwd: Path):
    p = cwd / "readme.md"
    p.write_text("# hi (: not python")
    assert await check(p) == []


def test_format_issues_empty():
    assert format_issues([]) == ""


def test_format_issues_caps_lines():
    issues = [f"err {i}" for i in range(30)]
    out = format_issues(issues)
    assert "more)" in out
    assert "fix them" in out


async def test_write_reports_python_syntax_error(tool_ctx: ToolContext):
    res = await WriteTool().run(
        WriteInput(file_path="broke.py", content="def f(:\n    pass\n"), tool_ctx
    )
    # File is still written (not an error result), but diagnostics are surfaced.
    assert not res.is_error
    assert "Diagnostics found issues" in res.content


async def test_write_clean_python_has_no_diagnostics(tool_ctx: ToolContext):
    res = await WriteTool().run(
        WriteInput(file_path="fine.py", content="x = 1\n"), tool_ctx
    )
    assert "Diagnostics found issues" not in res.content


async def test_diagnostics_disabled_by_config(cwd: Path, config):
    config.post_edit_diagnostics = False
    ctx = ToolContext(cwd=cwd, config=config)
    res = await WriteTool().run(
        WriteInput(file_path="broke.py", content="def f(:\n"), ctx
    )
    assert "Diagnostics found issues" not in res.content


async def test_edit_reports_diagnostics(tool_ctx: ToolContext, cwd: Path):
    (cwd / "m.py").write_text("value = 1\n")
    res = await EditTool().run(
        EditInput(file_path="m.py", old_string="value = 1", new_string="value = ("),
        tool_ctx,
    )
    assert not res.is_error
    assert "Diagnostics found issues" in res.content
