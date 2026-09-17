"""Keyless web search for course pages.

Backends are tried in order: an on-disk cache, DuckDuckGo's no-JavaScript HTML
endpoints over a plain HTTP GET, then a headless-browser fallback (Bing / DDG).
No API key is required.
"""
from __future__ import annotations

import base64
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from bs4 import BeautifulSoup

_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_DDG_ENDPOINTS = (
    "https://html.duckduckgo.com/html/?q={q}",
    "https://lite.duckduckgo.com/lite/?q={q}",
)
_BRAVE_URL = "https://search.brave.com/search?q={q}&source=web"
# Brave rate-limits rapid scraping (HTTP 429); keep a polite gap between calls.
_BRAVE_MIN_INTERVAL = 2.5
_brave_last = 0.0
_ENGINE_HOSTS = ("duckduckgo.com", "bing.com", "google.com", "yahoo.com",
                 "brave.com", "bravesoftware.com")
# Hosts that never host downloadable lecture decks — skip to save fetches.
_NONCOURSE_HOSTS = ("youtube.com", "youtu.be", "reddit.com", "quizlet.com",
                    "facebook.com", "twitter.com", "x.com", "linkedin.com",
                    "amazon.com", "pinterest.com", "instagram.com")
_BLOCK_MARKERS = (
    "anomaly",
    "are you a robot",
    "unusual traffic",
    "detected unusual",
    "challenge-platform",
)


@dataclass
class SearchHit:
    url: str
    title: str = ""


def _decode_ddg_redirect(href: str) -> str | None:
    if not href:
        return None
    if href.startswith("//"):
        href = "https:" + href
    if "duckduckgo.com/l/" in href or "uddg=" in href:
        u = parse_qs(urlsplit(href).query).get("uddg", [""])[0]
        return unquote(u) if u else None
    if href.startswith(("http://", "https://")):
        return href
    return None


def _decode_bing_redirect(href: str) -> str | None:
    if "bing.com/ck/a" not in href:
        if href.startswith(("http://", "https://")):
            return href
        return None
    u = parse_qs(urlsplit(href).query).get("u", [""])[0]
    if not u.startswith("a1"):
        return None
    blob = u[2:]
    blob += "=" * (-len(blob) % 4)
    try:
        return base64.urlsafe_b64decode(blob).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return None


def _collect(anchors, decode) -> list[SearchHit]:
    hits: list[SearchHit] = []
    seen: set[str] = set()
    for a in anchors:
        href = a.get("href")
        if not href:
            continue
        target = decode(href.strip())
        if not target or not target.startswith(("http://", "https://")):
            continue
        key = target.split("#", 1)[0].rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        hits.append(SearchHit(url=target, title=a.get_text(" ", strip=True)))
    return hits


def parse_results(html: str) -> list[SearchHit]:
    soup = BeautifulSoup(html, "lxml")
    anchors = soup.select("a.result__a, a.result-link")
    if not anchors:
        anchors = soup.select("a[href]")
    return _collect(anchors, _decode_ddg_redirect)


def parse_bing(html: str) -> list[SearchHit]:
    soup = BeautifulSoup(html, "lxml")
    anchors = soup.select("li.b_algo h2 a[href], li.b_algo div.b_title a[href]")
    return _collect(anchors, _decode_bing_redirect)


def _looks_blocked(html: str) -> bool:
    if not html:
        return True
    low = html.lower()
    return any(m in low for m in _BLOCK_MARKERS)


def _http_get(url: str, timeout: int) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def _ddg_via_http(query: str, timeout: int) -> list[SearchHit]:
    encoded = urllib.parse.quote(query)
    for template in _DDG_ENDPOINTS:
        try:
            html = _http_get(template.format(q=encoded), timeout)
        except (urllib.error.URLError, OSError):
            continue
        if _looks_blocked(html):
            continue
        hits = parse_results(html)
        if hits:
            return hits
        time.sleep(0.4)
    return []


def parse_brave(html: str) -> list[SearchHit]:
    soup = BeautifulSoup(html, "lxml")
    hits: list[SearchHit] = []
    seen: set[str] = set()
    for a in soup.select("a[href^='http']"):
        href = (a.get("href") or "").strip()
        if not href.startswith(("http://", "https://")):
            continue
        host = urlsplit(href).netloc.lower()
        if any(h in host for h in _ENGINE_HOSTS):
            continue
        if any(host == h or host.endswith("." + h) for h in _NONCOURSE_HOSTS):
            continue
        key = href.split("#", 1)[0].rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        hits.append(SearchHit(url=href, title=a.get_text(" ", strip=True)))
    return hits


def _brave_via_http(query: str, timeout: int, retries: int = 3) -> list[SearchHit] | None:
    """Primary backend: Brave Search HTML (works where DDG/Google are blocked).

    Returns a (possibly empty) list when the page was fetched successfully, or
    ``None`` when Brave could not be reached / was blocked, so the caller can
    fall back to another backend instead of treating a real miss as an error.
    """
    global _brave_last
    encoded = urllib.parse.quote(query)
    url = _BRAVE_URL.format(q=encoded)
    backoff = 5.0
    for attempt in range(retries):
        wait = _BRAVE_MIN_INTERVAL - (time.time() - _brave_last)
        if wait > 0:
            time.sleep(wait)
        try:
            html = _http_get(url, timeout)
            _brave_last = time.time()
        except urllib.error.HTTPError as err:
            _brave_last = time.time()
            if err.code == 429 and attempt < retries - 1:
                time.sleep(backoff)
                backoff *= 2
                continue
            return None
        except (urllib.error.URLError, OSError):
            return None
        if _looks_blocked(html):
            return None
        return parse_brave(html)
    return None


def _browser_search(query: str, timeout_ms: int) -> list[SearchHit]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception:  # noqa: BLE001
        return []
    encoded = urllib.parse.quote(query)
    targets = (
        (f"https://www.bing.com/search?q={encoded}&setlang=en-us&cc=us",
         "li.b_algo", parse_bing),
        (f"https://html.duckduckgo.com/html/?q={encoded}",
         "a.result__a", parse_results),
    )
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(user_agent=_USER_AGENT, locale="en-US")
            page = context.new_page()
            try:
                for url, ready, parse in targets:
                    try:
                        page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                        page.wait_for_selector(ready, timeout=8000)
                    except Exception:  # noqa: BLE001
                        continue
                    hits = parse(page.content())
                    if hits:
                        return hits
                return []
            finally:
                context.close()
                browser.close()
    except Exception:  # noqa: BLE001
        return []


def _cache_path(cache_dir, query: str) -> Path:
    digest = hashlib.sha256(query.lower().encode("utf-8")).hexdigest()[:16]
    return Path(cache_dir) / f"{digest}.json"


def _cache_get(cache_dir, query: str, max_age_s: int) -> list[SearchHit] | None:
    if not cache_dir:
        return None
    path = _cache_path(cache_dir, query)
    if not path.is_file():
        return None
    if max_age_s and time.time() - path.stat().st_mtime > max_age_s:
        return None
    try:
        data = json.loads(path.read_text("utf-8"))
        return [SearchHit(**d) for d in data]
    except Exception:  # noqa: BLE001
        return None


def _cache_put(cache_dir, query: str, hits: list[SearchHit]) -> None:
    if not cache_dir:
        return
    path = _cache_path(cache_dir, query)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(json.dumps([asdict(h) for h in hits]), "utf-8")
    except OSError:
        return


def web_search(
    query: str,
    limit: int = 12,
    timeout: int = 12,
    cache_dir: str | Path | None = None,
    use_browser: bool = True,
    cache_max_age_s: int = 604800,
) -> list[SearchHit]:
    """Search the web for *query*; return up to *limit* result hits (cached)."""
    cache_path_dir = Path(cache_dir) if cache_dir else None
    cached = _cache_get(cache_path_dir, query, cache_max_age_s)
    if cached is not None:
        return cached[:limit]

    hits: list[SearchHit] = []
    brave = None
    try:
        brave = _brave_via_http(query, timeout)
    except Exception:  # noqa: BLE001
        brave = None
    if brave is not None:
        hits = brave
    else:
        try:
            hits = _ddg_via_http(query, timeout)
        except Exception:  # noqa: BLE001
            hits = []
        if not hits and use_browser:
            hits = _browser_search(query, timeout * 1000)

    if hits:
        _cache_put(cache_path_dir, query, hits)
    return hits[:limit]
