"""Tests for the WebSearch and WebFetch tools (network mocked)."""

from __future__ import annotations

from kavi.tools.base import ToolContext
from kavi.tools.webfetch import WebFetchInput, WebFetchTool
from kavi.tools.websearch import WebSearchInput, WebSearchTool


async def test_websearch_empty_query(tool_ctx: ToolContext):
    res = await WebSearchTool().run(WebSearchInput(query="  "), tool_ctx)
    assert res.is_error


async def test_websearch_formats_results(monkeypatch, tool_ctx: ToolContext):
    tool = WebSearchTool()

    async def fake_search(query, num):
        return (
            [
                {"title": "Result A", "url": "https://a.test", "snippet": "snip a"},
                {"title": "Result B", "url": "https://b.test", "snippet": ""},
            ],
            "jina",
            "",
        )

    monkeypatch.setattr(tool, "_search", fake_search)
    res = await tool.run(WebSearchInput(query="python"), tool_ctx)
    assert not res.is_error
    assert "Result A" in res.content
    assert "https://a.test" in res.content
    assert "snip a" in res.content
    assert "cite the sources" in res.content


async def test_websearch_no_results(monkeypatch, tool_ctx: ToolContext):
    tool = WebSearchTool()

    async def fake_search(query, num):
        return [], "", "boom"

    monkeypatch.setattr(tool, "_search", fake_search)
    res = await tool.run(WebSearchInput(query="python"), tool_ctx)
    assert res.is_error
    assert "boom" in res.content


async def test_websearch_is_read_only_and_concurrent():
    assert WebSearchTool.is_read_only is True
    assert WebSearchTool.is_concurrency_safe is True


async def test_webfetch_rejects_non_http(tool_ctx: ToolContext):
    res = await WebFetchTool().run(WebFetchInput(url="ftp://x.test"), tool_ctx)
    assert res.is_error


async def test_webfetch_raw(monkeypatch, tool_ctx: ToolContext):
    tool = WebFetchTool()

    async def fake_read(url, cap):
        return "Example Title", "Hello page body.", "jina-reader", ""

    monkeypatch.setattr(tool, "_read", fake_read)
    res = await tool.run(WebFetchInput(url="https://x.test"), tool_ctx)
    assert not res.is_error
    assert "Example Title" in res.content
    assert "Hello page body." in res.content


async def test_webfetch_distill_uses_complete(monkeypatch, cwd, config):
    tool = WebFetchTool()

    async def fake_read(url, cap):
        return "T", "big page content", "jina-reader", ""

    async def fake_complete(system, user):
        assert "big page content" in user
        return "DISTILLED ANSWER"

    monkeypatch.setattr(tool, "_read", fake_read)
    ctx = ToolContext(cwd=cwd, config=config, complete=fake_complete)
    res = await tool.run(
        WebFetchInput(url="https://x.test", prompt="what is it?"), ctx
    )
    assert not res.is_error
    assert "DISTILLED ANSWER" in res.content


async def test_webfetch_distill_fallback_to_raw(monkeypatch, cwd, config):
    tool = WebFetchTool()

    async def fake_read(url, cap):
        return "T", "raw body text", "jina-reader", ""

    # No complete callable -> distillation unavailable -> raw fallback.
    ctx = ToolContext(cwd=cwd, config=config, complete=None)
    monkeypatch.setattr(tool, "_read", fake_read)
    res = await tool.run(
        WebFetchInput(url="https://x.test", prompt="what?"), ctx
    )
    assert not res.is_error
    assert "raw body text" in res.content
    assert "distill unavailable" in res.content
