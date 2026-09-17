"""Async page fetcher built on Playwright.

Returns both the raw static HTML and, when needed, the JS-rendered DOM so deck
links injected client-side are discovered. Polite: identifiable User-Agent,
per-domain rate limiting, retries, and an on-disk HTML cache.
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from config.settings import settings

try:
    from playwright.async_api import async_playwright
except Exception:  # pragma: no cover - import guard
    async_playwright = None  # type: ignore


@dataclass
class FetchResult:
    url: str
    html: str          # best available DOM (rendered if it was needed)
    raw_html: str      # static DOM as first captured
    status: int = 200
    from_cache: bool = False
    rendered: bool = False


_JS_SHELL_MARKERS = (
    "you need to have javascript enabled",
    "javascript is required",
    "requires javascript",
    "please enable javascript",
    "enable javascript to",
)


def _looks_unrendered(html: str) -> bool:
    low = html.lower()
    return any(m in low for m in _JS_SHELL_MARKERS)


def _host_matches(url: str, hosts: tuple[str, ...]) -> bool:
    host = urlparse(url).netloc.lower().split("@")[-1].split(":")[0]
    return any(host == h.lower() or host.endswith("." + h.lower()) for h in hosts)


def _cache_path(url: str) -> Path:
    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return settings.cache_dir / f"{key}.html"


class _DomainRateLimiter:
    def __init__(self, delay: float) -> None:
        self.delay = delay
        self._last: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, domain: str) -> asyncio.Lock:
        if domain not in self._locks:
            self._locks[domain] = asyncio.Lock()
        return self._locks[domain]

    async def wait(self, domain: str) -> None:
        async with self._lock(domain):
            now = time.monotonic()
            gap = now - self._last.get(domain, 0.0)
            if gap < self.delay:
                await asyncio.sleep(self.delay - gap)
            self._last[domain] = time.monotonic()


class Fetcher:
    """Async context-manager fetcher.

    Usage:
        async with Fetcher() as f:
            result = await f.fetch(url)
    """

    def __init__(self, cfg=settings.fetch) -> None:
        self.cfg = cfg
        self._limiter = _DomainRateLimiter(cfg.per_domain_delay)
        self._sem = asyncio.Semaphore(cfg.max_concurrency)
        self._pw = None
        self._browser = None
        self._context = None

    async def __aenter__(self) -> "Fetcher":
        if async_playwright is None:
            raise RuntimeError("playwright is not installed")
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=True)
        self._context = await self._browser.new_context(user_agent=self.cfg.user_agent)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    def _read_cache(self, url: str) -> str | None:
        if not self.cfg.use_cache:
            return None
        p = _cache_path(url)
        if p.exists():
            return p.read_text(encoding="utf-8", errors="replace")
        return None

    def _write_cache(self, url: str, html: str) -> None:
        if self.cfg.use_cache:
            _cache_path(url).write_text(html, encoding="utf-8", errors="replace")

    async def fetch(self, url: str) -> FetchResult | None:
        force_render = _host_matches(url, self.cfg.render_hosts)
        cached = self._read_cache(url)
        if cached is not None and not force_render:
            return FetchResult(url=url, html=cached, raw_html=cached, from_cache=True)

        domain = urlparse(url).netloc
        last_err: Exception | None = None
        for attempt in range(self.cfg.max_retries + 1):
            try:
                async with self._sem:
                    await self._limiter.wait(domain)
                    page = await self._context.new_page()
                    try:
                        resp = await page.goto(
                            url,
                            timeout=self.cfg.nav_timeout_ms,
                            wait_until="domcontentloaded",
                        )
                        status = resp.status if resp else 0
                        raw_html = await page.content()
                        html = raw_html
                        rendered = False
                        # Render via networkidle when the static DOM is a JS
                        # shell or the host is known to inject content via JS.
                        if _looks_unrendered(raw_html) or force_render:
                            try:
                                await page.wait_for_load_state(
                                    "networkidle", timeout=self.cfg.nav_timeout_ms
                                )
                                html = await page.content()
                                rendered = True
                            except Exception:  # noqa: BLE001 - keep raw html
                                pass
                    finally:
                        await page.close()
                self._write_cache(url, html)
                return FetchResult(
                    url=url, html=html, raw_html=raw_html,
                    status=status, rendered=rendered,
                )
            except Exception as err:  # noqa: BLE001 - retry any nav error
                last_err = err
                await asyncio.sleep(0.5 * (attempt + 1))
        return None
