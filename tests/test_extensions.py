"""Tests for the extension system: frontmatter, skills, hooks, commands, plugins."""

from __future__ import annotations

from pathlib import Path

from kavi.extensions import frontmatter as fm
from kavi.extensions.hooks import Hook, HookRunner
from kavi.extensions.skills import load_skills
from kavi.tools.base import ToolContext
from kavi.tools.skill import SkillInput, SkillTool


# ---------------------------------------------------------------- frontmatter
def test_frontmatter_parse():
    text = "---\nname: my-skill\ndescription: does things\ntags: [a, b]\n---\nBody here."
    meta, body = fm.parse(text)
    assert meta["name"] == "my-skill"
    assert meta["description"] == "does things"
    assert meta["tags"] == ["a", "b"]
    assert body == "Body here."


def test_frontmatter_no_block():
    meta, body = fm.parse("just body, no frontmatter")
    assert meta == {}
    assert body == "just body, no frontmatter"


def test_expand_template_arguments():
    out = fm.expand_template("Do $1 with $ARGUMENTS", args="foo bar")
    assert "Do foo" in out
    assert "foo bar" in out


def test_expand_template_file_mention(tmp_path: Path):
    (tmp_path / "notes.txt").write_text("SECRET_CONTENT")
    out = fm.expand_template("See @notes.txt please", workspace=tmp_path, run_bash=False)
    assert "SECRET_CONTENT" in out


# --------------------------------------------------------------------- skills
def test_load_skills(tmp_path: Path):
    sdir = tmp_path / ".kavi" / "skills" / "pdf"
    sdir.mkdir(parents=True)
    (sdir / "SKILL.md").write_text(
        "---\nname: pdf-processing\ndescription: work with PDFs\n---\nDetailed steps."
    )
    (sdir / "helper.py").write_text("print('x')")
    skills = load_skills(tmp_path)
    assert "pdf-processing" in skills
    skill = skills["pdf-processing"]
    assert "Detailed steps." in skill.body
    assert "helper.py" in skill.bundled_files()


async def test_skill_tool_loads_body(tmp_path: Path, config):
    sdir = tmp_path / ".kavi" / "skills" / "pdf"
    sdir.mkdir(parents=True)
    (sdir / "SKILL.md").write_text(
        "---\nname: pdf\ndescription: pdf work\n---\nFull instructions."
    )
    skills = load_skills(tmp_path)
    ctx = ToolContext(cwd=tmp_path, config=config, extras={"skills": skills})
    res = await SkillTool().run(SkillInput(name="pdf"), ctx)
    assert not res.is_error
    assert "Full instructions." in res.content


async def test_skill_tool_unknown(tmp_path: Path, config):
    ctx = ToolContext(cwd=tmp_path, config=config, extras={"skills": {}})
    res = await SkillTool().run(SkillInput(name="nope"), ctx)
    assert res.is_error


# ---------------------------------------------------------------------- hooks
def test_hook_matcher():
    h = Hook(event="PreToolUse", matcher="Bash|Write", command="x")
    assert h.matches("Bash")
    assert h.matches("Write")
    assert not h.matches("Read")
    assert Hook(event="Stop", matcher="", command="x").matches("anything")


def test_hookrunner_load(tmp_path: Path):
    hooks_dir = tmp_path / ".kavi"
    hooks_dir.mkdir()
    (hooks_dir / "hooks.json").write_text(
        '{"PreToolUse": [{"matcher": "Bash", "command": "true"}]}'
    )
    runner = HookRunner.load(tmp_path)
    assert runner.any_for("PreToolUse")
    assert not runner.any_for("Stop")


async def test_pretooluse_hook_blocks_on_nonzero(tmp_path: Path):
    import sys
    import json
    hooks_dir = tmp_path / ".kavi"
    hooks_dir.mkdir()
    script = f"{sys.executable} -c \"import sys; print('nope', file=sys.stderr); sys.exit(1)\""
    (hooks_dir / "hooks.json").write_text(json.dumps({"PreToolUse": [{"command": script}]}))
    runner = HookRunner.load(tmp_path)
    result = await runner.run("PreToolUse", tmp_path, "Bash", {"input": {}})
    assert result.blocked
    assert "nope" in result.message


async def test_pretooluse_hook_allows_on_zero(tmp_path: Path):
    import sys
    import json
    hooks_dir = tmp_path / ".kavi"
    hooks_dir.mkdir()
    script = f"{sys.executable} -c \"import sys; sys.exit(0)\""
    (hooks_dir / "hooks.json").write_text(json.dumps({"PreToolUse": [{"command": script}]}))
    runner = HookRunner.load(tmp_path)
    result = await runner.run("PreToolUse", tmp_path, "Bash", {"input": {}})
    assert not result.blocked


# ------------------------------------------------------------- user commands
def test_load_user_commands(tmp_path: Path):
    from kavi.extensions.usercommands import load_user_commands

    cdir = tmp_path / ".kavi" / "commands"
    (cdir / "git").mkdir(parents=True)
    (cdir / "review.md").write_text("---\ndescription: Review code\n---\nReview: $ARGUMENTS")
    (cdir / "git" / "commit.md").write_text("Commit the changes.")
    commands = load_user_commands(tmp_path, reserved={"help"})
    names = {c.name for c in commands}
    assert "review" in names
    assert "git:commit" in names


def test_user_command_does_not_shadow_builtin(tmp_path: Path):
    from kavi.extensions.usercommands import load_user_commands

    cdir = tmp_path / ".kavi" / "commands"
    cdir.mkdir(parents=True)
    (cdir / "help.md").write_text("custom help")
    commands = load_user_commands(tmp_path, reserved={"help"})
    assert all(c.name != "help" for c in commands)


# ---------------------------------------------------- external MCP import
def test_import_claude_project_mcp_json(tmp_path: Path):
    from kavi.extensions.mcp_import import discover_external_mcp

    (tmp_path / ".mcp.json").write_text(
        '{"mcpServers": {"fs": {"command": "npx", "args": ["-y", "server-fs"],'
        ' "env": {"ROOT": "/tmp"}}, "remote": {"url": "https://ex.com/mcp", "type": "http"}}}'
    )
    servers, origins = discover_external_mcp(tmp_path)
    assert servers["fs"].command == "npx"
    assert servers["fs"].args == ["-y", "server-fs"]
    assert servers["fs"].env == {"ROOT": "/tmp"}
    assert servers["remote"].url == "https://ex.com/mcp"
    assert servers["remote"].transport == "http"
    assert "claude" in origins["fs"]


def test_import_codex_config_toml(tmp_path: Path, monkeypatch):
    from kavi.extensions import mcp_import

    fake_home = tmp_path / "home"
    (fake_home / ".codex").mkdir(parents=True)
    (fake_home / ".codex" / "config.toml").write_text(
        '[mcp_servers.code-mcp]\n'
        'command = "aifx"\n'
        'args = ["mcp", "run"]\n'
        'env_vars = ["MY_TOKEN"]\n'
    )
    monkeypatch.setattr(mcp_import.Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.setenv("MY_TOKEN", "secret-123")

    servers, origins = mcp_import.discover_external_mcp(tmp_path)
    assert servers["code-mcp"].command == "aifx"
    assert servers["code-mcp"].args == ["mcp", "run"]
    # env_vars names are inherited from the current environment.
    assert servers["code-mcp"].env["MY_TOKEN"] == "secret-123"
    assert "codex" in origins["code-mcp"]


def test_import_native_config_wins_via_helpers():
    """Claude/Codex parsers reject entries with neither command nor url."""
    from kavi.extensions.mcp_import import _from_claude_entry, _from_codex_entry

    assert _from_claude_entry({"nonsense": 1}) is None
    assert _from_codex_entry({"nonsense": 1}) is None
    assert _from_claude_entry({"command": "x"}).transport == "stdio"
