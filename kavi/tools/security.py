"""Security scan tool - AI-powered vulnerability scanning for codebases.

This tool provides Claude Code Security-style scanning: it uses an LLM-powered
sub-agent to read the codebase, identify security vulnerabilities, classify them
by severity and confidence, and return structured findings.

Unlike rule-based SAST tools, this scanner understands *what the code does* and
can catch business-logic flaws, data-flow issues, and patterns that signature-
based tools miss.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from kavi.tools.base import Tool, ToolContext, ToolResult


# --------------------------------------------------------------------------- Input


class SecurityScanInput(BaseModel):
    path: str = Field(
        default=".",
        description=(
            "Directory or file to scan. Defaults to the current working directory. "
            "Use '.' to scan the whole project."
        ),
    )
    severity: str = Field(
        default="all",
        description=(
            "Minimum severity to report. One of: all, critical, high, medium, low. "
            "Findings below this threshold are filtered out."
        ),
    )
    file_pattern: str = Field(
        default="*",
        description=(
            "Glob pattern for files to include, e.g. '*.py' or '*.{py,js,ts}'. "
            "Defaults to all files."
        ),
    )
    max_files: int = Field(
        default=50,
        description=(
            "Maximum number of files to scan. Large codebases are sampled if needed. "
            "Increase for thorough scans at the cost of latency."
        ),
    )


class SecurityFindingsInput(BaseModel):
    findings: str = Field(
        description=(
            "Raw findings from SecurityScan, formatted as JSON list of "
            "finding objects. Each object should have: file, line (int), "
            "type (string), severity (string), confidence (float 0-1), "
            "description (string), cwe_id (string, optional)."
        ),
    )
    mode: str = Field(
        default="review",
        description=(
            "What to do with the findings. One of:\n"
            "  review  - return a human-readable summary (default)\n"
            "  json    - return machine-readable JSON\n"
            "  fix     - propose fixes for each finding (requires approval)"
        ),
    )


# ------------------------------------------------------------------------- Tool


_SECURITY_AGENT_SUFFIX = """\
You are a security research sub-agent. Your job is to deeply investigate the
codebase and find security vulnerabilities.

HOW TO SCAN:
1. Use Glob to find all relevant source files matching the pattern.
2. Use Read to examine each file carefully.
3. Use Grep to find dangerous patterns (hardcoded secrets, SQL concatenation,
   dangerous API usage, insecure defaults, etc.).
4. Trace data flow for user-controlled input (request params, stdin, env vars).

VULNERABILITY CATEGORIES TO CHECK:

[A1] Injection (SQL, Command, Code, XSS, LDAP, OS)
  - Python: string formatting in SQL queries, os.system(), eval(), exec(),
    pickle.loads(), yaml.load() without Loader=SafeLoader
  - JavaScript: innerHTML, document.write(), eval(), Function(), new Function()
  - Shell: subprocess with shell=True, os.system, shell=True

[A2] Broken Authentication & Session Management
  - Hardcoded passwords or API keys in source
  - Missing authentication on sensitive endpoints
  - Weak/default session secret

[A3] Sensitive Data Exposure
  - Secrets in environment variables that are committed
  - Logging sensitive data (passwords, tokens, PII)
  - Missing encryption for data at rest

[A4] XML External Entities (XXE)
  - XML parsers with insecure configurations

[A5] Broken Access Control
  - Missing permission checks on sensitive functions
  - IDOR: using user-provided IDs without verification
  - Path traversal: joining paths without validation

[A6] Security Misconfiguration
  - Debug mode enabled in production
  - Missing security headers (CORS, CSP, X-Content-Type-Options)
  - Default credentials

[A7] Cross-Site Scripting (XSS)
  - Reflected/stored XSS in web apps
  - Insecure React/Vue/Svelte patterns

[A8] Insecure Deserialization
  - pickle.loads(), yaml.load(Loader=None), JSON.parse of untrusted data

[A9] Using Components with Known Vulnerabilities
  - Outdated dependencies with known CVEs (flag if versions look old)

[A10] Insufficient Logging & Monitoring
  - Missing auth/logging on sensitive operations

ADDITIONAL PATTERNS:
- hardcoded secrets: api_key, password, secret, token, private_key patterns
- path traversal: open() with user-controlled paths without validation
- race conditions: file operations without atomicity
- insecure randomness: random.random() for security-sensitive purposes
- missing rate limiting: auth endpoints without rate limits
- SSRF: fetching URLs from user input
- template injection: string formatting in template engines

OUTPUT FORMAT:
Return your findings as a JSON list. Each finding must have:
{
  "file": "relative/path/file.py",
  "line": 42,
  "type": "SQL Injection",
  "severity": "high",        <-- critical | high | medium | low
  "confidence": 0.85,        <-- 0.0 to 1.0
  "description": "SQL query built by string concatenation with user input 'query' param",
  "cwe_id": "CWE-89",        <-- CWE identifier
  "code_snippet": "db.execute('SELECT * FROM users WHERE name = ' + username)",
  "fix_suggestion": "Use parameterized queries: db.execute('SELECT * FROM users WHERE name = ?', [username])"
}

Return ONLY the JSON. No preamble. No explanation. Start with '[' and end with ']'.
If no vulnerabilities are found, return an empty list: []
"""


class SecurityScanTool(Tool):
    name = "SecurityScan"
    description = """
    Scan a codebase for security vulnerabilities using AI-powered analysis.
    Unlike rule-based scanners, this tool understands code semantics and can catch
    business-logic flaws, injection risks, data-flow issues, and hardcoded secrets.
    Returns structured findings with severity (critical/high/medium/low) and confidence
    ratings. Use SecurityFindings to review, filter, or generate fixes for the findings.
    Read-only: does not modify any files.
    """
    InputModel = SecurityScanInput
    is_read_only = True
    is_concurrency_safe = False  # CPU-bound scanning; run one at a time

    def render_call(self, data: SecurityScanInput) -> str:  # type: ignore[override]
        return f"SecurityScan {data.path}"

    async def run(self, data: SecurityScanInput, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        base = self.resolve_path(ctx.cwd, data.path)

        # Discover files
        files = sorted(self._discover_files(base, data.file_pattern))[: data.max_files]

        if not files:
            return ToolResult(
                content="No files found matching the pattern. "
                "Try a different file_pattern or verify the path.",
                title="SecurityScan: no files found",
            )

        # Build the per-file content block for the sub-agent
        file_list = "\n".join(f"- {f}" for f in files)
        scan_prompt = (
            f"CODEBASE: {base}\n"
            f"FILES TO ANALYZE:\n{file_list}\n\n"
            f"File pattern: {data.file_pattern}\n"
            f"Minimum severity to report: {data.severity}\n\n"
            f"{_SECURITY_AGENT_SUFFIX}"
        )

        if ctx.run_subagent is None:
            return ToolResult.error(
                "SecurityScan requires a sub-agent backend but none is configured. "
                "This usually means Kavi is running without the full agent engine."
            )

        try:
            raw = await asyncio.wait_for(
                ctx.run_subagent(
                    prompt=scan_prompt,
                    tool_names=["Read", "Grep", "Glob", "WebSearch", "WebFetch"],
                    system_suffix="You are a security research sub-agent.",
                    agent_name="security-scanner",
                ),
                timeout=300.0,
            )
        except asyncio.TimeoutError:
            return ToolResult.error(
                "SecurityScan timed out after 5 minutes. "
                "Try reducing max_files or narrowing the path."
            )

        # Parse JSON from the sub-agent output
        findings = self._parse_findings(raw)
        if findings is None:
            return ToolResult(
                content=(
                    "SecurityScan completed but could not parse the model's output as JSON.\n\n"
                    "Raw output:\n"
                    + raw[:2000]
                ),
                title="SecurityScan: parse error",
                display=f"Scan ran on {len(files)} files but results could not be parsed.",
            )

        # Store findings in extras so SecurityFindings can pick them up
        ctx.extras["_security_scan_findings"] = findings

        summary = self._summarize_findings(findings, len(files))
        return ToolResult(
            content=summary,
            title=f"SecurityScan: {len(findings)} finding(s) in {len(files)} file(s)",
            display=summary,
            ui_payload={
                "type": "security_scan",
                "findings": findings,
                "file_count": len(files),
                "path": str(base),
            },
        )

    def _discover_files(self, base: Path, pattern: str) -> list[str]:
        """Return sorted list of relative paths matching pattern under base."""
        try:
            files: list[str] = []
            if pattern == "*":
                # Match all source files
                suffixes = (
                    "*.py", "*.js", "*.mjs", "*.cjs", "*.ts", "*.tsx",
                    "*.jsx", "*.go", "*.rs", "*.java", "*.rb", "*.php",
                    "*.cs", "*.cpp", "*.c", "*.h", "*.hpp", "*.swift",
                    "*.kt", "*.kts", "*.sh", "*.bash", "*.ps1", "*.yaml",
                    "*.yml", "*.toml", "*.json", "*.env*",
                )
                for glob_pat in suffixes:
                    files.extend(str(p.relative_to(base)) for p in base.rglob(glob_pat) if p.is_file())
            else:
                files.extend(str(p.relative_to(base)) for p in base.rglob(pattern) if p.is_file())
            return sorted(set(files))
        except Exception:  # noqa: BLE001
            return []

    def _parse_findings(self, raw: str) -> list[dict[str, Any]] | None:
        """Extract JSON list from sub-agent output."""
        # Try to find a JSON array in the output
        raw = raw.strip()
        # Find the first '[' and last ']'
        start = raw.find("[")
        end = raw.rfind("]")
        if start == -1 or end == -1:
            return None
        try:
            parsed = json.loads(raw[start : end + 1])
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass
        return None

    def _severity_rank(self, sev: str) -> int:
        return {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(sev.lower(), 4)

    def _is_sev_reported(self, sev: str, threshold: str) -> bool:
        if threshold == "all":
            return True
        return self._severity_rank(sev) <= self._severity_rank(threshold)

    def _summarize_findings(
        self, findings: list[dict[str, Any]], file_count: int
    ) -> str:
        if not findings:
            return (
                f"✅ SecurityScan complete — no vulnerabilities found in {file_count} file(s).\n"
                "Note: this does not guarantee the codebase is secure, only that the scanner\n"
                "did not detect common vulnerability patterns."
            )

        # Group by severity
        by_sev: dict[str, list[dict[str, Any]]] = {}
        for f in findings:
            sev = f.get("severity", "unknown")
            by_sev.setdefault(sev, []).append(f)

        lines = [
            f"🔍 SecurityScan complete — **{len(findings)}** finding(s) in **{file_count}** file(s)\n",
        ]
        for sev in ["critical", "high", "medium", "low"]:
            if sev not in by_sev:
                continue
            items = by_sev[sev]
            icon = {"critical": "🚨", "high": "⚠️", "medium": "⚡", "low": "💡"}[sev]
            lines.append(f"\n{icon} **{sev.upper()}** ({len(items)} finding{'s' if len(items) > 1 else ''})")
            for item in items[:10]:
                cwe = item.get("cwe_id", "")
                cwe_str = f" [{cwe}]" if cwe else ""
                lines.append(
                    f"  • {item['file']}:{item.get('line', '?')} — {item.get('type', 'Unknown')}{cwe_str}\n"
                    f"    {item.get('description', '')[:120]}"
                )
            if len(items) > 10:
                lines.append(f"  ... and {len(items) - 10} more {sev} findings")

        lines.append(
            "\nUse SecurityFindings to review, filter by severity, or generate "
            "patch suggestions for any finding above."
        )
        return "\n".join(lines)


class SecurityFindingsTool(Tool):
    name = "SecurityFindings"
    description = """
    Process and interact with the results from a previous SecurityScan call.
    Use this to:
    - Review findings filtered by severity or type
    - Export findings as formatted JSON
    - Generate concrete fix suggestions (patches) for each finding

    This tool reads the findings stored by SecurityScan in the session context.
    Always call SecurityScan first before using this tool.
    """
    InputModel = SecurityFindingsInput
    is_read_only = False
    is_concurrency_safe = True

    def render_call(self, data: SecurityFindingsInput) -> str:  # type: ignore[override]
        return f"SecurityFindings mode={data.mode}"

    async def run(self, data: SecurityFindingsInput, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        findings: list[dict[str, Any]] | None = ctx.extras.get("_security_scan_findings")
        if findings is None:
            return ToolResult.error(
                "No SecurityScan findings found in session. "
                "Run SecurityScan first to populate findings."
            )

        try:
            parsed: list[dict[str, Any]] = json.loads(data.findings)
        except json.JSONDecodeError as exc:
            return ToolResult.error(f"Invalid findings JSON: {exc}")

        if data.mode == "json":
            formatted = json.dumps(parsed, indent=2)
            return ToolResult(
                content=formatted,
                title=f"SecurityFindings JSON ({len(parsed)} findings)",
            )

        if data.mode == "review":
            return self._review_findings(parsed)

        if data.mode == "fix":
            return await self._generate_fixes(parsed, ctx)

        return ToolResult.error(f"Unknown mode: {data.mode}. Use: review | json | fix")

    def _review_findings(self, findings: list[dict[str, Any]]) -> ToolResult:
        if not findings:
            return ToolResult(content="No findings to display.", title="SecurityFindings: empty")

        lines = [
            f"📋 Security Review — {len(findings)} Finding(s)\n",
            "=" * 60,
        ]
        for i, f in enumerate(findings, 1):
            cwe = f.get("cwe_id", "N/A")
            sev = f.get("severity", "unknown").upper()
            conf = f.get("confidence", 0)
            lines.append(
                f"\n[{i}] {sev} — {f.get('type', 'Unknown')} (conf: {conf:.0%}) [{cwe}]"
                f"\n  File:     {f['file']}:{f.get('line', '?')}"
                f"\n  Issue:    {f.get('description', 'No description')}"
                f"\n  Code:     {f.get('code_snippet', 'N/A')[:100]}"
                f"\n  Fix:      {f.get('fix_suggestion', 'No suggestion')[:120]}"
            )
        return ToolResult(
            content="\n".join(lines),
            title=f"SecurityFindings: {len(findings)} finding(s)",
        )

    async def _generate_fixes(
        self, findings: list[dict[str, Any]], ctx: ToolContext
    ) -> ToolResult:
        """Generate fix suggestions for each finding using the LLM."""
        if not findings or ctx.complete is None:
            return ToolResult.error(
                "No findings to fix, or no LLM helper available."
            )

        fix_lines = ["🛠️ Security Fix Proposals\n", "=" * 60, ""]

        for i, f in enumerate(findings, 1):
            file = f.get("file", "unknown")
            line = f.get("line", 0)
            vuln_type = f.get("type", "Unknown vulnerability")
            description = f.get("description", "")
            snippet = f.get("code_snippet", "")
            suggestion = f.get("fix_suggestion", "")
            cwe = f.get("cwe_id", "")

            prompt = (
                f"You are a security expert. Provide a concrete, production-ready fix.\n\n"
                f"Vulnerability: {vuln_type}\n"
                f"{('CWE: ' + cwe) if cwe else ''}\n"
                f"File: {file}\n"
                f"Line: {line}\n"
                f"Description: {description}\n"
                f"Insecure code:\n```\n{snippet}\n```\n"
                f"Fix suggestion: {suggestion}\n\n"
                "Return a JSON object with exactly this structure:\n"
                "{\n"
                '  "explanation": "2-3 sentence explanation of the fix",\n'
                '  "fixed_code": "the corrected, production-ready code block"\n'
                "}\n"
                "Start directly with '{'."
            )

            response = await ctx.complete(
                "You are a security expert. Return ONLY the JSON object.",
                prompt,
            )

            try:
                parsed_resp = json.loads(response.strip())
                explanation = parsed_resp.get("explanation", "No explanation available.")
                fixed_code = parsed_resp.get("fixed_code", snippet)
            except json.JSONDecodeError:
                explanation = "(Could not parse fix from model)"
                fixed_code = snippet

            fix_lines.extend([
                f"\n[{i}] {vuln_type} — {file}:{line}",
                f"    CWE: {cwe}",
                f"    {description}",
                f"\n    Explanation: {explanation}",
                f"\n    Fixed code:\n    ```\n    {fixed_code}\n    ```",
            ])

        fix_lines.append(
            "\n\n⚠️ Review each fix carefully before applying. "
            "Test fixes in isolation and verify with SecurityScan after applying."
        )
        return ToolResult(
            content="\n".join(fix_lines),
            title=f"SecurityFindings Fixes: {len(findings)} proposal(s)",
            display="\n".join(fix_lines),
        )