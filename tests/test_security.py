"""Tests for the SecurityScan and SecurityFindings tools."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from kavi.tools.base import ToolContext
from kavi.tools.security import (
    SecurityFindingsInput,
    SecurityFindingsTool,
    SecurityScanInput,
    SecurityScanTool,
)


# --------------------------------------------------------------------------- Fixtures


@pytest.fixture
def security_tool_ctx(tool_ctx: ToolContext) -> ToolContext:
    """A ToolContext with run_subagent and complete wired to return safe defaults."""
    tool_ctx.run_subagent = AsyncMock(return_value="[]")
    tool_ctx.complete = AsyncMock(return_value='{"explanation": "test", "fixed_code": "x = 1"}')
    return tool_ctx


# --------------------------------------------------------------------------- SecurityScanTool


class TestSecurityScanTool:
    def test_schema(self):
        schema = SecurityScanTool().to_schema()
        assert schema.name == "SecurityScan"
        props = schema.input_schema["properties"]
        assert "path" in props
        assert "severity" in props
        assert "file_pattern" in props
        assert "max_files" in props

    def test_input_defaults(self):
        inp = SecurityScanInput()
        assert inp.path == "."
        assert inp.severity == "all"
        assert inp.file_pattern == "*"
        assert inp.max_files == 50

    def test_severity_enum_values(self):
        for sev in ("all", "critical", "high", "medium", "low"):
            inp = SecurityScanInput(severity=sev)
            assert inp.severity == sev

    async def test_no_subagent(self, tool_ctx: ToolContext, cwd: Path):
        """Gracefully errors when no run_subagent is available."""
        (cwd / "sample.py").write_text("x = 1")
        tool = SecurityScanTool()
        res = await tool.run(SecurityScanInput(path="."), tool_ctx)
        assert res.is_error
        assert "sub-agent backend" in res.content

    async def test_no_files_found(self, tool_ctx: ToolContext):
        """Returns a helpful message when no files match the pattern."""
        tool_ctx.run_subagent = AsyncMock(return_value="[]")
        tool = SecurityScanTool()
        res = await tool.run(
            SecurityScanInput(path=".", file_pattern="*.nonexistent"),
            tool_ctx,
        )
        assert not res.is_error
        assert "no files found" in res.content.lower()

    async def test_parse_error_returns_raw_output(self, tool_ctx: ToolContext, cwd: Path):
        """When sub-agent output isn't valid JSON, returns the raw output for inspection."""
        (cwd / "sample.py").write_text("x = 1")
        tool_ctx.run_subagent = AsyncMock(return_value="Here are my findings: boom not json")
        tool = SecurityScanTool()
        res = await tool.run(SecurityScanInput(path="."), tool_ctx)
        assert "could not parse" in res.content.lower()

    async def test_successful_scan_with_findings(self, tool_ctx: ToolContext, cwd: Path):
        """Happy path: sub-agent returns valid JSON findings, stored in extras."""
        (cwd / "auth.py").write_text("API_KEY = 'sk-123'")
        tool_ctx.run_subagent = AsyncMock(
            return_value=json.dumps([
                {
                    "file": "auth.py",
                    "line": 42,
                    "type": "Hardcoded Secret",
                    "severity": "critical",
                    "confidence": 0.95,
                    "description": "API key hardcoded in source",
                    "cwe_id": "CWE-798",
                    "code_snippet": "API_KEY = 'sk-123'",
                    "fix_suggestion": "Use os.environ['API_KEY']",
                }
            ])
        )
        tool = SecurityScanTool()
        res = await tool.run(SecurityScanInput(path="."), tool_ctx)
        assert not res.is_error
        assert "1 finding" in res.content or "finding" in res.content.lower()
        assert tool_ctx.extras["_security_scan_findings"] is not None
        assert len(tool_ctx.extras["_security_scan_findings"]) == 1

    async def test_empty_findings_returns_clean_message(self, tool_ctx: ToolContext, cwd: Path):
        """When no vulnerabilities found, returns an encouraging clean message."""
        (cwd / "clean.py").write_text("x = 1")
        tool_ctx.run_subagent = AsyncMock(return_value="[]")
        tool = SecurityScanTool()
        res = await tool.run(SecurityScanInput(path="."), tool_ctx)
        assert not res.is_error
        assert "no vulnerabilities" in res.content.lower() or "✅" in res.content

    async def test_timeout_is_handled(self, tool_ctx: ToolContext, cwd: Path):
        """Times out gracefully after 5 minutes instead of hanging."""
        import asyncio

        (cwd / "sample.py").write_text("x = 1")

        # Use AsyncMock with a side_effect that simulates what
        # asyncio.wait_for does when the inner coroutine takes too long:
        # it raises asyncio.TimeoutError.
        tool_ctx.run_subagent = AsyncMock(
            side_effect=asyncio.TimeoutError("simulated timeout")
        )
        tool = SecurityScanTool()
        res = await tool.run(SecurityScanInput(path="."), tool_ctx)
        assert res.is_error
        assert "timed out" in res.content.lower()


# --------------------------------------------------------------------------- SecurityFindingsTool


class TestSecurityFindingsTool:
    def test_schema(self):
        schema = SecurityFindingsTool().to_schema()
        assert schema.name == "SecurityFindings"
        props = schema.input_schema["properties"]
        assert "findings" in props
        assert "mode" in props

    def test_input_defaults(self):
        inp = SecurityFindingsInput(findings="[]")
        assert inp.mode == "review"
        assert inp.findings == "[]"

    async def test_no_prior_scan_error(self, tool_ctx: ToolContext):
        """Errors when SecurityScan hasn't been called first."""
        tool = SecurityFindingsTool()
        res = await tool.run(
            SecurityFindingsInput(findings="[]", mode="review"),
            tool_ctx,
        )
        assert res.is_error
        assert "no securityscan findings" in res.content.lower()

    async def test_json_mode(self, tool_ctx: ToolContext):
        """Returns raw JSON when mode=json."""
        tool_ctx.extras["_security_scan_findings"] = [
            {"file": "a.py", "line": 1, "type": "XSS", "severity": "high", "confidence": 0.8,
             "description": "innerHTML usage", "cwe_id": "CWE-79", "code_snippet": "", "fix_suggestion": ""}
        ]
        tool = SecurityFindingsTool()
        findings_json = json.dumps(tool_ctx.extras["_security_scan_findings"])
        res = await tool.run(
            SecurityFindingsInput(findings=findings_json, mode="json"),
            tool_ctx,
        )
        assert not res.is_error
        assert '"file": "a.py"' in res.content

    async def test_review_mode_empty(self, tool_ctx: ToolContext):
        """Review mode handles empty findings list gracefully."""
        tool_ctx.extras["_security_scan_findings"] = []
        tool = SecurityFindingsTool()
        res = await tool.run(
            SecurityFindingsInput(findings="[]", mode="review"),
            tool_ctx,
        )
        assert not res.is_error
        assert "no findings" in res.content.lower()

    async def test_review_mode_shows_finding_details(self, tool_ctx: ToolContext):
        """Review mode renders each finding with all key fields."""
        finding = {
            "file": "login.py",
            "line": 17,
            "type": "SQL Injection",
            "severity": "high",
            "confidence": 0.9,
            "description": "User input concatenated into SQL query",
            "cwe_id": "CWE-89",
            "code_snippet": "db.execute('SELECT * FROM u WHERE n=' + name)",
            "fix_suggestion": "Use parameterized query",
        }
        tool_ctx.extras["_security_scan_findings"] = [finding]
        tool = SecurityFindingsTool()
        res = await tool.run(
            SecurityFindingsInput(findings=json.dumps([finding]), mode="review"),
            tool_ctx,
        )
        assert "SQL Injection" in res.content
        assert "CWE-89" in res.content
        assert "login.py:17" in res.content

    async def test_fix_mode_generates_proposals(self, tool_ctx: ToolContext):
        """Fix mode calls the LLM helper and returns structured fix proposals."""
        finding = {
            "file": "auth.py",
            "line": 5,
            "type": "Hardcoded Secret",
            "severity": "critical",
            "confidence": 0.95,
            "description": "API key hardcoded in source",
            "cwe_id": "CWE-798",
            "code_snippet": "API_KEY = 'sk-123'",
            "fix_suggestion": "Use environment variable",
        }
        tool_ctx.extras["_security_scan_findings"] = [finding]
        tool_ctx.complete = AsyncMock(
            return_value=json.dumps({
                "explanation": "Use environment variable instead.",
                "fixed_code": "import os\nAPI_KEY = os.environ['API_KEY']",
            })
        )
        tool = SecurityFindingsTool()
        res = await tool.run(
            SecurityFindingsInput(findings=json.dumps([finding]), mode="fix"),
            tool_ctx,
        )
        assert not res.is_error
        assert "fixed code" in res.content.lower()

    async def test_unknown_mode_error(self, tool_ctx: ToolContext):
        """Unknown mode returns a clear error."""
        tool_ctx.extras["_security_scan_findings"] = []
        tool = SecurityFindingsTool()
        res = await tool.run(
            SecurityFindingsInput(findings="[]", mode="unknown"),
            tool_ctx,
        )
        assert res.is_error
        assert "unknown mode" in res.content.lower()

    async def test_invalid_json_findings_error(self, tool_ctx: ToolContext):
        """Malformed findings JSON returns a parse error."""
        tool_ctx.extras["_security_scan_findings"] = []
        tool = SecurityFindingsTool()
        res = await tool.run(
            SecurityFindingsInput(findings="not valid json{", mode="review"),
            tool_ctx,
        )
        assert res.is_error
        assert "invalid findings json" in res.content.lower()


# --------------------------------------------------------------------------- Severity helpers (internal)


class TestSecurityScanToolHelpers:
    def test_severity_rank_order(self, tool_ctx: ToolContext):
        """_severity_rank orders critical < high < medium < low."""
        tool_ctx.run_subagent = AsyncMock(return_value="[]")
        tool = SecurityScanTool()
        ranks = {s: tool._severity_rank(s) for s in ["critical", "high", "medium", "low"]}
        assert ranks["critical"] < ranks["high"] < ranks["medium"] < ranks["low"]

    def test_is_sev_reported_threshold_all(self, tool_ctx: ToolContext):
        """Threshold 'all' reports everything."""
        tool_ctx.run_subagent = AsyncMock(return_value="[]")
        tool = SecurityScanTool()
        assert tool._is_sev_reported("critical", "all")
        assert tool._is_sev_reported("low", "all")

    def test_is_sev_reported_threshold_filtering(self, tool_ctx: ToolContext):
        """Threshold 'high' filters out medium and low."""
        tool_ctx.run_subagent = AsyncMock(return_value="[]")
        tool = SecurityScanTool()
        assert tool._is_sev_reported("critical", "high")
        assert tool._is_sev_reported("high", "high")
        assert not tool._is_sev_reported("medium", "high")
        assert not tool._is_sev_reported("low", "high")

    def test_parse_findings_valid_json(self):
        tool = SecurityScanTool()
        raw = '[{"file": "a.py", "line": 1}]'
        result = tool._parse_findings(raw)
        assert result == [{"file": "a.py", "line": 1}]

    def test_parse_findings_with_preamble(self):
        """Finds JSON array even when preceded/followed by other text."""
        tool = SecurityScanTool()
        raw = 'Here are my findings: [{"file": "a.py", "line": 1}]\nAll done.'
        result = tool._parse_findings(raw)
        assert result == [{"file": "a.py", "line": 1}]

    def test_parse_findings_invalid(self):
        tool = SecurityScanTool()
        assert tool._parse_findings("not json at all") is None
        assert tool._parse_findings('{"not": "array"}') is None

    def test_discover_files_pattern_star(self, tmp_path: Path):
        tool = SecurityScanTool()
        (tmp_path / "a.py").write_text("x=1")
        (tmp_path / "b.js").write_text("y=2")
        (tmp_path / "c.ts").write_text("z=3")
        files = tool._discover_files(tmp_path, "*")
        assert "a.py" in files
        assert "b.js" in files
        assert "c.ts" in files

    def test_discover_files_specific_pattern(self, tmp_path: Path):
        tool = SecurityScanTool()
        (tmp_path / "a.py").write_text("x=1")
        (tmp_path / "b.js").write_text("y=2")
        files = tool._discover_files(tmp_path, "*.py")
        assert "a.py" in files
        assert "b.js" not in files

    def test_discover_files_nested(self, tmp_path: Path):
        tool = SecurityScanTool()
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "nested.py").write_text("x=1")
        files = tool._discover_files(tmp_path, "*")
        assert any("nested.py" in f for f in files)