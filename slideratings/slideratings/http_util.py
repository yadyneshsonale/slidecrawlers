"""Polite HTTP fetching for slideratings.

Plain ``urllib`` GET with a desktop User-Agent, on-disk caching, per-host rate
limiting and retry/backoff. When a page looks like it needs JavaScript (or a
caller requires it) the request transparently falls back to a headless
Playwright render so server-rendered and client-rendered pages both work.
"""
from __future__ import annotations

import hashlib
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from .config import HttpConfig

_JS_MARKERS = (
    "please enable javascript",
    "you need to enable javascript",
    "enable javascript to run this app",
)

_last_request: dict[str, float] = {}


def _host(url: str) -> str:
    return urlsplit(url).netloc.lower()


def _throttle(url: str, min_interval_s: float) -> None:
    host = _host(url)
    now = time.time()
    prev = _last_request.get(host)
    if prev is not None:
        wait = min_interval_s - (now - prev)
        if wait > 0:
            time.sleep(wait)
    _last_request[host] = time.time()


def _cache_path(cache_dir: Path, url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
    return cache_dir / f"{digest}.html"


def _cache_read(cache_dir: Path | None, url: str, max_age_s: int) -> str | None:
    if not cache_dir:
        return None
    path = _cache_path(cache_dir, url)
    if not path.is_file():
        return None
    if max_age_s and time.time() - path.stat().st_mtime > max_age_s:
        return None
    try:
        return path.read_text("utf-8", errors="replace")
    except OSError:
        return None


def _cache_write(cache_dir: Path | None, url: str, html: str) -> None:
    if not cache_dir or not html:
        return
    path = _cache_path(cache_dir, url)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(html, "utf-8")
    except OSError:
        return


def _plain_get(url: str, cfg: HttpConfig) -> str:
    headers = {
        "User-Agent": cfg.user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    last_err: Exception | None = None
    for attempt in range(cfg.max_retries):
        _throttle(url, cfg.min_interval_s)
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=cfg.timeout_s) as resp:
                charset = resp.headers.get_content_charset() or "utf-8"
                return resp.read().decode(charset, errors="replace")
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(0.6 * (attempt + 1))
    raise RuntimeError(f"GET failed for {url}: {last_err}")


def _needs_render(html: str) -> bool:
    if not html or len(html.strip()) < 200:
        return True
    low = html.lower()
    return any(marker in low for marker in _JS_MARKERS)


def _render(url: str, cfg: HttpConfig) -> str | None:
    try:
        from playwright.sync_api import sync_playwright
    except Exception:  # noqa: BLE001
        return None
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(user_agent=cfg.user_agent, locale="en-US")
            page = context.new_page()
            page.goto(url, timeout=cfg.timeout_s * 1000, wait_until="domcontentloaded")
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:  # noqa: BLE001
                pass
            html = page.content()
            context.close()
            browser.close()
            return html
    except Exception:  # noqa: BLE001
        return None


def get_html(
    url: str,
    cfg: HttpConfig,
    cache_dir: Path | None = None,
    force_render: bool = False,
    use_cache: bool = True,
) -> str:
    """Fetch ``url`` as HTML, using cache, retries and render fallback."""
    if use_cache:
        cached = _cache_read(cache_dir, url, cfg.cache_max_age_s)
        if cached is not None:
            return cached

    html = ""
    if not force_render:
        try:
            html = _plain_get(url, cfg)
        except Exception:  # noqa: BLE001
            html = ""

    if (force_render or _needs_render(html)) and cfg.render_fallback:
        rendered = _render(url, cfg)
        if rendered:
            html = rendered

    if html:
        _cache_write(cache_dir, url, html)
    return html
