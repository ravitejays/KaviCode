"""WebFetch tool - fetch a web page and extract its content.

Two modes:
  * **distill** (when a ``prompt`` is given): a small/fast model reads the FULL
    page and returns only the relevant answer - far more context-efficient than
    dumping raw text, and able to see more of the page (mirrors Claude Code's
    WebFetch);
  * **raw** (no ``prompt``): return the page's cleaned text, truncated.

Prefers the zero-key Jina Reader upstream (clean markdown) and falls back to a
direct fetch with stdlib HTML stripping. Read-only and network-bound.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from kavi.tools.base import Tool, ToolContext, ToolResult
from kavi.tools.websearch import jina_headers

MAX_CHARS = 20_000
# When distilling, the small model may read far more of the page than we would
# ever return raw - coverage goes up while main-context cost goes down.
DISTILL_INPUT_CHARS = 100_000
_READ_TIMEOUT = 30

_DISTILL_SYSTEM = (
    "You are a web-content extraction assistant. You are given the text of a web "
    "page and a request. Answer the request using ONLY the page content. Be "
    "concise and faithful: extract the relevant facts, quote key passages when "
    "useful, and preserve important links (as markdown). If the page does not "
    "contain the answer, say so explicitly. Never invent information that is not "
    "on the page."
)

_SCRIPT_STYLE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\n\s*\n\s*\n+")
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def _html_to_text(html: str) -> str:
    text = _SCRIPT_STYLE.sub("", html)
    text = _TAG.sub("", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    return _WS.sub("\n\n", text).strip()


class WebFetchInput(BaseModel):
    url: str = Field(description="Absolute http(s) URL to fetch.")
    prompt: str | None = Field(
        default=None,
        description=(
            "What to extract or answer from the page. When given, the page is "
            "distilled by a small model and only the focused answer is returned. "
            "Omit to get raw cleaned text."
        ),
    )
    max_chars: int = Field(
        default=MAX_CHARS,
        description="Max characters of raw text to return when no 'prompt' is given.",
    )


class WebFetchTool(Tool):
    name = "WebFetch"
    description = """
    Fetch a web page and extract its content. Pass a 'prompt' describing what you want from
    the page (a question to answer or data to extract) and a small fast model reads the FULL
    page and returns only the relevant, distilled answer - far more context-efficient than
    dumping raw text. Omit 'prompt' to get the page's raw cleaned text (truncated). Use after
    WebSearch to read a promising result, or on any http(s) URL. Read-only.
    """
    InputModel = WebFetchInput
    is_read_only = True
    is_concurrency_safe = True

    def permission_subject(self, data: WebFetchInput) -> str:  # type: ignore[override]
        return data.url

    def render_call(self, data: WebFetchInput) -> str:  # type: ignore[override]
        if data.prompt:
            return f"WebFetch {data.url} - {data.prompt}"
        return f"WebFetch {data.url}"

    async def run(self, data: WebFetchInput, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        if urlparse(data.url).scheme not in {"http", "https"}:
            return ToolResult.error("URL must start with http:// or https://")
        prompt = (data.prompt or "").strip()
        max_chars = max(500, data.max_chars)

        fetch_cap = DISTILL_INPUT_CHARS if prompt else max_chars
        title, text, backend, error = await self._read(data.url, fetch_cap)
        if not text:
            return ToolResult.error(error or "Could not read page.")

        header = f"# {title}\n({data.url})\n\n" if title else f"({data.url})\n\n"

        if prompt:
            distilled = await self._distill(ctx, data.url, title, text, prompt)
            if distilled:
                return ToolResult(
                    content=header + distilled,
                    title=f"WebFetch {data.url}",
                    display=f"distilled {len(distilled)} chars (via {backend})",
                )
            # Distillation unavailable/failed - fall back to raw text.
            text = text[:max_chars]
            note = "[distill unavailable - returning raw text]\n\n"
            return ToolResult(
                content=header + note + text,
                title=f"WebFetch {data.url}",
                display=f"read {len(text)} chars (raw fallback)",
            )

        text = text[:max_chars]
        return ToolResult(
            content=header + text,
            title=f"WebFetch {data.url}",
            display=f"read {len(text)} chars (via {backend})",
        )

    # -- distillation ------------------------------------------------------------
    async def _distill(
        self, ctx: ToolContext, url: str, title: str, text: str, prompt: str
    ) -> str:
        """Summarise the page against ``prompt`` via the small/fast model. '' on failure."""
        if ctx.complete is None:
            return ""
        head = f"Title: {title}\nURL: {url}\n\n" if title else f"URL: {url}\n\n"
        user = f"{head}REQUEST:\n{prompt}\n\nPAGE CONTENT:\n{text[:DISTILL_INPUT_CHARS]}"
        try:
            return (await ctx.complete(_DISTILL_SYSTEM, user)).strip()
        except Exception:  # noqa: BLE001
            return ""

    # -- backends ----------------------------------------------------------------
    async def _read(self, url: str, max_chars: int) -> tuple[str, str, str, str]:
        title, text, jerr = await self._jina_read(url, max_chars)
        if text:
            return title, text, "jina-reader", ""
        title, text, derr = await self._direct_read(url, max_chars)
        if text:
            return title, text, "direct", ""
        return "", "", "", derr or jerr or "could not read page"

    async def _jina_read(self, url: str, max_chars: int) -> tuple[str, str, str]:
        try:
            import httpx

            async with httpx.AsyncClient(timeout=_READ_TIMEOUT) as client:
                resp = await client.get(
                    f"https://r.jina.ai/{url}", headers=jina_headers(json_accept=False)
                )
            if resp.status_code != 200:
                return "", "", f"jina reader HTTP {resp.status_code}"
            body = re.sub(r"\n{3,}", "\n\n", resp.text).strip()
            m = re.search(r"^Title:\s*(.+)$", body, re.MULTILINE)
            title = m.group(1).strip() if m else ""
            return title, body[:max_chars], ""
        except Exception as exc:  # noqa: BLE001
            return "", "", f"jina reader: {exc}"

    async def _direct_read(self, url: str, max_chars: int) -> tuple[str, str, str]:
        try:
            import httpx

            async with httpx.AsyncClient(follow_redirects=True, timeout=20) as client:
                resp = await client.get(
                    url, headers={"User-Agent": "Kavi/0.1 (+Bahumukh AI)", "Accept-Language": "en"}
                )
                resp.raise_for_status()
                ctype = resp.headers.get("content-type", "")
                raw = resp.text
        except Exception as exc:  # noqa: BLE001
            return "", "", f"fetch failed: {exc}"

        m = _TITLE.search(raw)
        title = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
        text = _html_to_text(raw) if "html" in ctype or "<html" in raw[:1000].lower() else raw
        return title, text[:max_chars], ""
