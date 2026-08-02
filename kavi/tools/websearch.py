"""WebSearch tool - search the live web and return ranked results.

Kavi's "Agent Reach": prefer a high-quality, zero-API-key upstream
(Jina Search, reached over plain HTTP via httpx) and gracefully fall back to a
fully-local DuckDuckGo backend when the upstream is rate-limited or unavailable.
The primary path needs no extra dependencies beyond ``httpx`` (already a core
dependency); if the optional ``ddgs`` package is installed it enriches the
fallback.

Read-only and concurrency-safe, so it never triggers a permission prompt -
matching the UX of a built-in web search.
"""

from __future__ import annotations

import asyncio
import os
from urllib.parse import quote

from pydantic import BaseModel, Field

from kavi.tools.base import Tool, ToolContext, ToolResult

_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_SEARCH_TIMEOUT = 25


def jina_key() -> str:
    """Read the optional Jina API key at call time (raises rate limits)."""
    return os.getenv("JINA_API_KEY", "").strip()


def jina_headers(json_accept: bool = True) -> dict[str, str]:
    headers = {"User-Agent": _USER_AGENT}
    if json_accept:
        headers["Accept"] = "application/json"
    key = jina_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


class WebSearchInput(BaseModel):
    query: str = Field(description="Search query.")
    num_results: int = Field(
        default=8, description="How many results to return (1-15).", ge=1, le=15
    )


class WebSearchTool(Tool):
    name = "WebSearch"
    description = """
    Search the live web and return ranked results (title, URL, snippet). Use for current
    events, facts, documentation, products, people - anything you don't already know or
    that may have changed since training. Follow up with WebFetch to read a promising
    result. Read-only.
    """
    InputModel = WebSearchInput
    is_read_only = True
    is_concurrency_safe = True

    def permission_subject(self, data: WebSearchInput) -> str:  # type: ignore[override]
        return data.query

    def render_call(self, data: WebSearchInput) -> str:  # type: ignore[override]
        return f"WebSearch {data.query!r}"

    async def run(self, data: WebSearchInput, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        query = data.query.strip()
        if not query:
            return ToolResult.error("Empty query.")
        num = max(1, min(data.num_results, 15))

        results, backend, error = await self._search(query, num)
        if not results:
            return ToolResult.error(error or "No results found.")

        lines = [f"Search results for: {query}  (via {backend})", ""]
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r['title']}".rstrip())
            lines.append(f"   {r['url']}")
            if r.get("snippet"):
                lines.append(f"   {r['snippet']}")
            lines.append("")
        lines.append("REMINDER: cite the sources you use in your answer as markdown links.")
        return ToolResult(
            content="\n".join(lines).rstrip(),
            title=f"WebSearch {query!r}",
            display=f"{len(results)} results (via {backend})",
        )

    # -- backends ----------------------------------------------------------------
    async def _search(self, query: str, num: int) -> tuple[list[dict[str, str]], str, str]:
        jina, jerr = await self._jina_search(query, num)
        if jina:
            return jina, "jina", ""
        ddg, derr = await self._ddg_search(query, num)
        if ddg:
            return ddg, "duckduckgo", ""
        return [], "", derr or jerr or "no results"

    async def _jina_search(self, query: str, num: int) -> tuple[list[dict[str, str]], str]:
        try:
            import httpx

            async with httpx.AsyncClient(timeout=_SEARCH_TIMEOUT) as client:
                resp = await client.get(
                    f"https://s.jina.ai/?q={quote(query)}", headers=jina_headers()
                )
            if resp.status_code != 200:
                return [], f"jina search HTTP {resp.status_code}"
            rows = (resp.json().get("data") or [])[:num]
            out = [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "snippet": (r.get("description") or r.get("content") or "")[:500],
                }
                for r in rows
            ]
            return out, "" if out else "jina returned 0 results"
        except Exception as exc:  # noqa: BLE001
            return [], f"jina: {exc}"

    async def _ddg_search(self, query: str, num: int) -> tuple[list[dict[str, str]], str]:
        try:
            from ddgs import DDGS  # type: ignore[import-not-found]
        except ImportError:
            try:
                from duckduckgo_search import DDGS  # type: ignore[import-not-found]
            except ImportError:
                return [], "ddgs not installed (pip install ddgs)"

        def _run() -> tuple[list[dict[str, str]], str]:
            last_error = ""
            for backend in ("duckduckgo", "bing", "brave", "yahoo", "google"):
                try:
                    out: list[dict[str, str]] = []
                    with DDGS() as ddgs:
                        try:
                            it = ddgs.text(
                                query, backend=backend, safesearch="off", max_results=num
                            )
                        except TypeError:
                            it = ddgs.text(query, safesearch="off", max_results=num)
                        for r in it:
                            out.append(
                                {
                                    "title": r.get("title", ""),
                                    "url": r.get("href") or r.get("url") or "",
                                    "snippet": r.get("body") or r.get("snippet") or "",
                                }
                            )
                    if out:
                        return out, ""
                except Exception as exc:  # noqa: BLE001
                    last_error = f"{backend}: {exc}"
            return [], last_error or "all DuckDuckGo backends returned 0 results"

        # ddgs is synchronous; run it off the event loop.
        return await asyncio.to_thread(_run)
