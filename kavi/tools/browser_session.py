"""Persistent Playwright browser session manager for Kavi (ASYNC version).

A single browser process and a single browser context (with a shared cookie jar
and storage) are kept alive for the lifetime of the agent turn — or optionally
across turns if the caller holds the singleton. Pages are created on demand and
always closed after use to cap memory usage.

Design goals
------------
* **One browser process** — launching Chromium is expensive (~300 ms); we
  launch it once and reuse it across tool calls in the same session.
* **Shared session state** — cookies / localStorage survive across calls so
  multi-step flows (login → navigate → extract) work naturally.
* **Async-native** — uses Playwright's async API so it works naturally inside
  KaviCode's asyncio event loop without blocking.
* **Graceful absence** — if playwright is not installed the module imports fine;
  the guard is deferred to the first actual use, where a clear error is raised
  instead of an ``ImportError`` at module load time.
* **Auto-restart** — if the browser crashes or is closed externally the manager
  transparently relaunches it on the next call.
* **Resource cleanup** — ``shutdown()`` is called from the agent/REPL teardown
  path.
"""

from __future__ import annotations

import asyncio
import base64
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import Browser, BrowserContext, Page, Playwright

# Module-level singleton — one manager per process.
_manager: "BrowserSessionManager | None" = None
_manager_lock = asyncio.Lock()


async def get_manager() -> "BrowserSessionManager":
    """Return (or lazily create) the process-wide browser session manager."""
    global _manager
    async with _manager_lock:
        if _manager is None:
            _manager = BrowserSessionManager()
        return _manager


async def shutdown_manager() -> None:
    """Shut down the singleton manager (called on agent/REPL exit)."""
    global _manager
    async with _manager_lock:
        if _manager is not None:
            try:
                await _manager.shutdown()
            except Exception:  # noqa: BLE001
                pass
            _manager = None


class BrowserSessionManager:
    """Manages a single long-lived Playwright browser + context pair.

    All public methods use asyncio.Lock to serialize browser operations.
    """

    VIEWPORT = {"width": 1280, "height": 800}
    NAVIGATION_TIMEOUT = 30_000
    NETWORK_IDLE_TIMEOUT = 10_000
    MAX_SCREENSHOT_BYTES = 4 * 1024 * 1024

    def __init__(self) -> None:
        self._op_lock = asyncio.Lock()
        self._pw: "Playwright | None" = None
        self._browser: "Browser | None" = None
        self._context: "BrowserContext | None" = None
        self._storage_state: dict[str, Any] | None = None
        self._launch_count = 0

    # ------------------------------------------------------------------ public

    async def navigate(
        self,
        url: str,
        *,
        wait_until: str = "domcontentloaded",
        timeout_ms: int | None = None,
    ) -> tuple[str, str, bytes]:
        """Open `url` in a fresh page and return (title, text, screenshot_png)."""
        async with self._op_lock:
            page = await self._new_page()
            try:
                to = timeout_ms or self.NAVIGATION_TIMEOUT
                await page.goto(url, wait_until=wait_until, timeout=to)
                await self._wait_settled(page)
                title = await page.title() or ""
                text = await self._extract_text(page)
                png = await self._screenshot(page)
                return title, text, png
            finally:
                await self._close_page(page)

    async def click(
        self,
        url: str,
        selector: str,
        *,
        timeout_ms: int | None = None,
    ) -> tuple[str, str, bytes]:
        """Navigate to `url`, click `selector`, return (title, text, screenshot)."""
        async with self._op_lock:
            page = await self._new_page()
            try:
                to = timeout_ms or self.NAVIGATION_TIMEOUT
                await page.goto(url, wait_until="domcontentloaded", timeout=to)
                await self._wait_settled(page)
                await page.locator(selector).first.scroll_into_view_if_needed(timeout=5_000)
                await page.locator(selector).first.click(timeout=5_000)
                await self._wait_settled(page)
                title = await page.title() or ""
                text = await self._extract_text(page)
                png = await self._screenshot(page)
                return title, text, png
            finally:
                await self._close_page(page)

    async def fill_and_submit(
        self,
        url: str,
        fields: dict[str, str],
        submit_selector: str | None = None,
        *,
        timeout_ms: int | None = None,
    ) -> tuple[str, str, bytes]:
        """Navigate to `url`, fill form `fields`, optionally click submit."""
        async with self._op_lock:
            page = await self._new_page()
            try:
                to = timeout_ms or self.NAVIGATION_TIMEOUT
                await page.goto(url, wait_until="domcontentloaded", timeout=to)
                await self._wait_settled(page)
                last_loc = None
                for sel, val in fields.items():
                    loc = page.locator(sel).first
                    await loc.scroll_into_view_if_needed(timeout=5_000)
                    await loc.fill(val, timeout=5_000)
                    last_loc = loc
                if submit_selector:
                    await page.locator(submit_selector).first.click(timeout=5_000)
                elif last_loc is not None:
                    await last_loc.press("Enter", timeout=5_000)
                await self._wait_settled(page)
                title = await page.title() or ""
                text = await self._extract_text(page)
                png = await self._screenshot(page)
                return title, text, png
            finally:
                await self._close_page(page)

    async def screenshot_only(self, url: str, *, timeout_ms: int | None = None) -> tuple[str, str, bytes]:
        """Thin wrapper: navigate + return title/text/screenshot."""
        return await self.navigate(url, timeout_ms=timeout_ms)

    async def execute_js(
        self,
        url: str,
        script: str,
        *,
        timeout_ms: int | None = None,
    ) -> tuple[str, Any, bytes]:
        """Navigate to `url`, run `script`, return (title, result, screenshot)."""
        async with self._op_lock:
            page = await self._new_page()
            try:
                to = timeout_ms or self.NAVIGATION_TIMEOUT
                await page.goto(url, wait_until="domcontentloaded", timeout=to)
                await self._wait_settled(page)
                result = await page.evaluate(script)
                await self._wait_settled(page)
                title = await page.title() or ""
                png = await self._screenshot(page)
                return title, result, png
            finally:
                await self._close_page(page)

    async def clear_session(self) -> None:
        """Wipe cookies and storage."""
        async with self._op_lock:
            self._storage_state = None
            if self._context is not None:
                try:
                    await self._context.clear_cookies()
                    await self._context.clear_permissions()
                except Exception:  # noqa: BLE001
                    pass

    async def shutdown(self) -> None:
        """Close the browser and Playwright runtime cleanly."""
        async with self._op_lock:
            await self._teardown()

    # ----------------------------------------------------------------- private

    async def _ensure_browser(self) -> None:
        """Launch the Playwright browser if it isn't running (or has crashed)."""
        if self._browser is not None and self._browser.is_connected():
            return

        await self._teardown_browser()

        if self._pw is None:
            try:
                from playwright.async_api import async_playwright  # noqa: PLC0415
            except ImportError as exc:
                raise RuntimeError(
                    "playwright is not installed. "
                    "Run: pip install 'playwright>=1.40' && playwright install chromium"
                ) from exc
            self._pw = await async_playwright().start()

        self._browser = await self._pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-setuid-sandbox",
                "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        ctx_kwargs: dict[str, Any] = {
            "viewport": self.VIEWPORT,
            "user_agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "accept_downloads": False,
            "java_script_enabled": True,
            "ignore_https_errors": True,
        }
        if self._storage_state:
            ctx_kwargs["storage_state"] = self._storage_state
        self._context = await self._browser.new_context(**ctx_kwargs)
        await self._context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        self._launch_count += 1

    async def _new_page(self) -> "Page":
        await self._ensure_browser()
        assert self._context is not None
        page = await self._context.new_page()
        page.set_default_timeout(self.NAVIGATION_TIMEOUT)
        page.set_default_navigation_timeout(self.NAVIGATION_TIMEOUT)
        return page

    async def _close_page(self, page: "Page") -> None:
        """Save cookies/storage then close the page."""
        try:
            if self._context is not None:
                self._storage_state = await self._context.storage_state()
        except Exception:  # noqa: BLE001
            pass
        try:
            await page.close()
        except Exception:  # noqa: BLE001
            pass

    async def _wait_settled(self, page: "Page") -> None:
        """Wait briefly for any pending network activity to drain."""
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=5_000)
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(0.4)

    async def _extract_text(self, page: "Page") -> str:
        """Pull visible text from the page."""
        try:
            script = """
() => {
    const clone = document.documentElement.cloneNode(true);
    ['script','style','noscript','head','meta','link','svg','iframe',
     'header','footer','nav','aside'].forEach(tag => {
        clone.querySelectorAll(tag).forEach(el => el.remove());
    });
    return (clone.innerText || clone.textContent || '')
        .replace(/\\n{3,}/g, '\\n\\n')
        .replace(/[ \\t]+/g, ' ')
        .trim()
        .slice(0, 50000);
}
"""
            return str(await page.evaluate(script) or "")
        except Exception:  # noqa: BLE001
            return ""

    async def _screenshot(self, page: "Page") -> bytes:
        """Capture a viewport screenshot, scaling down if it exceeds the size cap."""
        try:
            png = await page.screenshot(
                type="png",
                full_page=False,
                clip={"x": 0, "y": 0, "width": self.VIEWPORT["width"], "height": self.VIEWPORT["height"]},
            )
            if len(png) <= self.MAX_SCREENSHOT_BYTES:
                return png
            png = await page.screenshot(
                type="png",
                full_page=False,
                scale="css",
            )
            return png
        except Exception:  # noqa: BLE001
            return b""

    async def _teardown_browser(self) -> None:
        for attr in ("_context", "_browser"):
            target = getattr(self, attr, None)
            if target is not None:
                try:
                    await target.close()
                except Exception:  # noqa: BLE001
                    pass
                setattr(self, attr, None)

    async def _teardown(self) -> None:
        await self._teardown_browser()
        if self._pw is not None:
            try:
                await self._pw.stop()
            except Exception:  # noqa: BLE001
                pass
            self._pw = None


def png_to_data_url(png: bytes) -> str:
    """Convert raw PNG bytes to a base64 data URL for the vision model."""
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")
