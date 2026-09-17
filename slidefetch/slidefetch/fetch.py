"""Fetch a page's HTML, rendering JavaScript when needed.

Uses Playwright headless Chromium so slide links injected by client-side
frameworks are still discovered. Returns both the raw and rendered DOM.
"""
from __future__ import annotations

from dataclasses import dataclass

try:
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover - import guard
    sync_playwright = None  # type: ignore

_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 slidefetch/1.0"
)

_JS_SHELL_MARKERS = (
    "you need to have javascript enabled",
    "javascript is required",
    "requires javascript",
    "please enable javascript",
    "enable javascript to",
)


@dataclass
class FetchResult:
    url: str
    html: str          # best available DOM (rendered if rendering was needed)
    raw_html: str      # static DOM as first captured
    status: int
    rendered: bool


def _looks_unrendered(html: str) -> bool:
    low = html.lower()
    return any(m in low for m in _JS_SHELL_MARKERS)


def fetch(url: str, timeout_ms: int = 30000, force_render: bool = False) -> FetchResult:
    """Load *url* and return its HTML.

    Always reads the static DOM first; re-renders with networkidle when the page
    looks like a JS shell or *force_render* is set.
    """
    if sync_playwright is None:
        raise RuntimeError("playwright is not installed (pip install -r requirements.txt)")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(user_agent=_USER_AGENT)
        page = context.new_page()
        try:
            resp = page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            status = resp.status if resp else 0
            raw_html = page.content()
            html = raw_html
            rendered = False
            if force_render or _looks_unrendered(raw_html):
                try:
                    page.wait_for_load_state("networkidle", timeout=timeout_ms)
                    html = page.content()
                    rendered = True
                except Exception:  # noqa: BLE001 - keep the raw html
                    pass
            return FetchResult(url=url, html=html, raw_html=raw_html,
                               status=status, rendered=rendered)
        finally:
            context.close()
            browser.close()
