"""Browser automation tool — gives Kavi a real browser with vision."""

from __future__ import annotations

import textwrap
from typing import Any
from urllib.parse import urlparse
from pydantic import BaseModel, Field

from kavi.messages import ImageBlock
from kavi.tools.base import Tool, ToolContext, ToolResult

_PLAYWRIGHT_INSTALL_MSG = (
    "Playwright is not installed. Install it with:\n"
    "    pip install 'playwright>=1.40'\n"
    "    playwright install chromium\n"
    "Then retry."
)

_MAX_TEXT_CHARS = 12_000
_ALLOWED_SCHEMES = {"http", "https"}


def _validate_url(url: str) -> str:
    url = url.strip()
    if not url:
        raise ValueError("url is required")
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ValueError(f"Only http(s) URLs are allowed (got scheme '{parsed.scheme}').")
    return url


async def _get_manager():
    try:
        from kavi.tools.browser_session import get_manager
        return await get_manager()
    except RuntimeError as exc:
        raise RuntimeError(str(exc)) from exc
    except ImportError as exc:
        raise RuntimeError(_PLAYWRIGHT_INSTALL_MSG) from exc


def _stage_screenshot(ctx: ToolContext, png: bytes, label: str) -> None:
    if not png:
        return
    if ctx.stage_user_content is None:
        return

    import base64
    encoded = base64.b64encode(png).decode("ascii")
    ctx.stage_user_content(ImageBlock(data=encoded, media_type="image/png"))


def _build_result(
    title: str,
    text: str,
    png: bytes,
    ctx: ToolContext,
    action: str,
    url: str,
) -> ToolResult:
    _stage_screenshot(ctx, png, f"{action} · {url}")

    page_text = text[:_MAX_TEXT_CHARS]
    if len(text) > _MAX_TEXT_CHARS:
        page_text += f"\n… (text truncated at {_MAX_TEXT_CHARS} chars)"

    screenshot_note = (
        "A screenshot of the rendered page has been attached to the next "
        "message — read it to see what the page looks like visually."
        if png else
        "(Screenshot unavailable.)"
    )

    content = (
        f"URL: {url}\n"
        f"Title: {title or '(no title)'}\n"
        f"\n{screenshot_note}\n"
        f"\n--- Page text ---\n{page_text}"
    )
    display = f"{action} → {title or url} ({len(png):,} bytes screenshot)"
    return ToolResult(content=content, display=display)


class BrowserNavigateInput(BaseModel):
    url: str = Field(description="Absolute http(s) URL to open.")
    wait_until: str = Field(
        default="domcontentloaded",
        description="When to consider navigation complete. 'domcontentloaded' (default), 'load', or 'networkidle'."
    )
    timeout: int = Field(default=30, description="Navigation timeout in seconds.")

class BrowserNavigateTool(Tool):
    name = "BrowserNavigate"
    description = textwrap.dedent("""\
        Open a URL in a real Chromium browser and return the rendered page
        title, visible text, and a screenshot — so you can SEE the page exactly
        as a human would. Use this for:
          • JavaScript-heavy / SPA pages that web_fetch cannot render
          • Pages that require cookies / session state
          • Visual layout inspection
        The screenshot is shown to you in the next message (requires a
        vision-capable model).
    """).strip()
    InputModel = BrowserNavigateInput
    is_read_only = True
    is_concurrency_safe = False

    def permission_subject(self, data: BrowserNavigateInput) -> str:  # type: ignore[override]
        return data.url

    def render_call(self, data: BrowserNavigateInput) -> str:  # type: ignore[override]
        return f"BrowserNavigate {data.url}"

    async def run(self, data: BrowserNavigateInput, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        try:
            url = _validate_url(data.url)
        except Exception as exc:
            return ToolResult.error(str(exc))
            
        wait_until = data.wait_until if data.wait_until in ("domcontentloaded", "load", "networkidle") else "domcontentloaded"
        timeout_ms = data.timeout * 1_000

        try:
            mgr = await _get_manager()
            title, text, png = await mgr.navigate(url, wait_until=wait_until, timeout_ms=timeout_ms)
        except Exception as exc:
            return ToolResult.error(f"Browser navigation failed: {exc}")

        return _build_result(title, text, png, ctx, "navigate", url)


class BrowserScreenshotInput(BaseModel):
    url: str = Field(description="Absolute http(s) URL to screenshot.")
    timeout: int = Field(default=30, description="Navigation timeout in seconds.")

class BrowserScreenshotTool(Tool):
    name = "BrowserScreenshot"
    description = textwrap.dedent("""\
        Navigate to a URL and return only the screenshot — useful when you
        just want to see what a page looks like without needing the full text.
    """).strip()
    InputModel = BrowserScreenshotInput
    is_read_only = True
    is_concurrency_safe = False

    def permission_subject(self, data: BrowserScreenshotInput) -> str:  # type: ignore[override]
        return data.url

    def render_call(self, data: BrowserScreenshotInput) -> str:  # type: ignore[override]
        return f"BrowserScreenshot {data.url}"

    async def run(self, data: BrowserScreenshotInput, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        try:
            url = _validate_url(data.url)
        except Exception as exc:
            return ToolResult.error(str(exc))
        timeout_ms = data.timeout * 1_000

        try:
            mgr = await _get_manager()
            title, text, png = await mgr.screenshot_only(url, timeout_ms=timeout_ms)
        except Exception as exc:
            return ToolResult.error(f"Browser screenshot failed: {exc}")

        return _build_result(title, text, png, ctx, "screenshot", url)


class BrowserClickInput(BaseModel):
    url: str = Field(description="Absolute http(s) URL to open before clicking.")
    selector: str = Field(description="Playwright locator expression for the element to click.")
    timeout: int = Field(default=30, description="Timeout in seconds.")

class BrowserClickTool(Tool):
    name = "BrowserClick"
    description = textwrap.dedent("""\
        Open a URL in a real browser, find an element by CSS selector, click it, 
        and return the resulting page state as text + screenshot.
    """).strip()
    InputModel = BrowserClickInput
    is_read_only = False
    is_concurrency_safe = False

    def permission_subject(self, data: BrowserClickInput) -> str:  # type: ignore[override]
        return data.url

    def render_call(self, data: BrowserClickInput) -> str:  # type: ignore[override]
        return f"BrowserClick {data.selector} on {data.url}"

    async def run(self, data: BrowserClickInput, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        try:
            url = _validate_url(data.url)
        except Exception as exc:
            return ToolResult.error(str(exc))
        if not data.selector.strip():
            return ToolResult.error("selector is required")
        timeout_ms = data.timeout * 1_000

        try:
            mgr = await _get_manager()
            title, text, png = await mgr.click(url, data.selector, timeout_ms=timeout_ms)
        except Exception as exc:
            return ToolResult.error(f"Browser click failed: {exc}")

        return _build_result(title, text, png, ctx, f"click({data.selector})", url)


class BrowserFillInput(BaseModel):
    url: str = Field(description="Absolute http(s) URL of the form page.")
    fields: dict[str, str] = Field(description="Mapping of Playwright locator -> value to type.")
    submit_selector: str | None = Field(default=None, description="Selector for the submit button.")
    timeout: int = Field(default=30, description="Timeout in seconds.")

class BrowserFillTool(Tool):
    name = "BrowserFill"
    description = textwrap.dedent("""\
        Open a URL in a real browser, fill one or more form fields, optionally
        click a submit button (or press Enter), and return the resulting page.
    """).strip()
    InputModel = BrowserFillInput
    is_read_only = False
    is_concurrency_safe = False

    def permission_subject(self, data: BrowserFillInput) -> str:  # type: ignore[override]
        return data.url

    def render_call(self, data: BrowserFillInput) -> str:  # type: ignore[override]
        keys = ", ".join(list(data.fields.keys())[:3])
        return f"BrowserFill [{keys}] on {data.url}"

    async def run(self, data: BrowserFillInput, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        try:
            url = _validate_url(data.url)
        except Exception as exc:
            return ToolResult.error(str(exc))
        if not data.fields:
            return ToolResult.error("fields must be a non-empty object mapping selector -> value")
        timeout_ms = data.timeout * 1_000

        try:
            mgr = await _get_manager()
            title, text, png = await mgr.fill_and_submit(
                url, data.fields, data.submit_selector, timeout_ms=timeout_ms
            )
        except Exception as exc:
            return ToolResult.error(f"Browser form fill failed: {exc}")

        return _build_result(title, text, png, ctx, "fill+submit", url)


class BrowserJsInput(BaseModel):
    url: str = Field(description="Absolute http(s) URL to open.")
    script: str = Field(description="JavaScript expression evaluated in the page.")
    timeout: int = Field(default=30, description="Navigation timeout in seconds.")

class BrowserJsTool(Tool):
    name = "BrowserJs"
    description = textwrap.dedent("""\
        Navigate to a URL, execute a JavaScript expression or function body in
        the page context, and return the result plus a screenshot.
    """).strip()
    InputModel = BrowserJsInput
    is_read_only = False
    is_concurrency_safe = False

    def permission_subject(self, data: BrowserJsInput) -> str:  # type: ignore[override]
        return data.url

    def render_call(self, data: BrowserJsInput) -> str:  # type: ignore[override]
        short = data.script[:60].replace("\n", " ")
        return f"BrowserJs `{short}` on {data.url}"

    async def run(self, data: BrowserJsInput, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        try:
            url = _validate_url(data.url)
        except Exception as exc:
            return ToolResult.error(str(exc))
        if not data.script.strip():
            return ToolResult.error("script is required")
        timeout_ms = data.timeout * 1_000

        try:
            mgr = await _get_manager()
            title, result, png = await mgr.execute_js(url, data.script, timeout_ms=timeout_ms)
        except Exception as exc:
            return ToolResult.error(f"Browser JS execution failed: {exc}")

        _stage_screenshot(ctx, png, f"js · {url}")

        import json as _json
        try:
            result_str = _json.dumps(result, indent=2, ensure_ascii=False)
        except (TypeError, ValueError):
            result_str = str(result)

        content = (
            f"URL: {url}\n"
            f"Title: {title or '(no title)'}\n"
            f"\nA screenshot has been attached to the next message.\n"
            f"\n--- Script result ---\n{result_str}"
        )
        display = f"js result: {result_str[:80]}"
        return ToolResult(content=content, display=display)


class BrowserClearSessionInput(BaseModel):
    pass

class BrowserClearSessionTool(Tool):
    name = "BrowserClearSession"
    description = textwrap.dedent("""\
        Clear all browser cookies, localStorage, and session state accumulated
        by previous browser tool calls.
    """).strip()
    InputModel = BrowserClearSessionInput
    is_read_only = False
    is_concurrency_safe = False

    def permission_subject(self, data: BrowserClearSessionInput) -> str:  # type: ignore[override]
        return ""

    def render_call(self, data: BrowserClearSessionInput) -> str:  # type: ignore[override]
        return "BrowserClearSession"

    async def run(self, data: BrowserClearSessionInput, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        try:
            mgr = await _get_manager()
            await mgr.clear_session()
        except Exception as exc:
            return ToolResult.error(f"Could not clear browser session: {exc}")
        return ToolResult(
            content="Browser session cleared. Cookies and storage have been wiped.",
            display="session cleared",
        )
