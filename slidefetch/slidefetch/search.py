"""Web search for course pages.

Given a query like ``"computer science course lecture slides"`` this returns a
list of result URLs. Backends are tried in order:

  0. Tavily (https://tavily.com) -- an API built for agents that returns clean,
     relevant result URLs. Used first when a ``TAVILY_API_KEY`` is available
     (env var or passed explicitly). Avoids the rate-limiting and geo-localised
     junk that scraping search engines suffers from.
  1. DuckDuckGo's no-JavaScript HTML endpoints over a plain HTTP GET (fast,
     clean links, no browser).
  2. A real headless browser (Playwright) querying Bing / DuckDuckGo, used only
     as a fallback when the above are rate-limited or return nothing.

Results are cached on disk per-query so re-runs and repeated disciplines stay
polite and reproducible. The HTML-parsing steps are pure functions so they can
be unit-tested against saved fixtures.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from bs4 import BeautifulSoup

# A clean, standard desktop UA. A custom product token tends to trip search
# engines' bot detection, so we deliberately do not advertise slidefetch here.
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Tavily search API (https://docs.tavily.com). Key comes from TAVILY_API_KEY.
_TAVILY_ENDPOINT = "https://api.tavily.com/search"

# DuckDuckGo no-JS endpoints. Both return static HTML and wrap real URLs in a
# ``//duckduckgo.com/l/?uddg=<encoded>`` redirect.
_DDG_ENDPOINTS = (
    "https://html.duckduckgo.com/html/?q={q}",
    "https://lite.duckduckgo.com/lite/?q={q}",
)

# Search-engine / redirector hosts whose own links are never real results.
_ENGINE_HOSTS = ("duckduckgo.com", "bing.com", "google.com", "yahoo.com")

# Substrings that mark an anti-bot / challenge page rather than real results.
_BLOCK_MARKERS = (
    "anomaly", "are you a robot", "unusual traffic",
    "detected unusual", "challenge-platform",
)


@dataclass
class SearchHit:
    url: str
    title: str


# --------------------------------------------------------------------------- #
# Link decoding + HTML parsing (pure)
# --------------------------------------------------------------------------- #

def _decode_ddg_redirect(href: str) -> str | None:
    """Turn a DuckDuckGo redirect href into the real destination URL."""
    if href.startswith("//"):
        href = "https:" + href
    parts = urlsplit(href)
    host = parts.netloc.lower()
    if any(host.endswith(h) for h in _ENGINE_HOSTS):
        target = parse_qs(parts.query).get("uddg")
        return unquote(target[0]) if target else None
    if parts.scheme in ("http", "https"):
        return href  # already a direct link
    return None


def _decode_bing_redirect(href: str) -> str | None:
    """Decode a Bing ``/ck/a?...&u=a1<base64url>`` click-tracking link."""
    if "bing.com/ck/a" not in href:
        return href if href.startswith(("http://", "https://")) else None
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
        target = decode((a.get("href") or "").strip())
        if not target or not target.startswith(("http://", "https://")):
            continue
        key = target.split("#", 1)[0].rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        hits.append(SearchHit(url=target, title=a.get_text(" ", strip=True)))
    return hits


def parse_results(html: str) -> list[SearchHit]:
    """Extract result URLs/titles from a DuckDuckGo HTML/lite results page."""
    soup = BeautifulSoup(html, "lxml")
    anchors = soup.select("a.result__a, a.result-link")
    if not anchors:
        anchors = [a for a in soup.find_all("a", href=True) if "uddg=" in a["href"]]
    return _collect(anchors, _decode_ddg_redirect)


def parse_bing(html: str) -> list[SearchHit]:
    """Extract result URLs/titles from a Bing results page."""
    soup = BeautifulSoup(html, "lxml")
    anchors = soup.select("li.b_algo h2 a[href], li.b_algo div.b_title a[href]")
    return _collect(anchors, _decode_bing_redirect)


def _looks_blocked(html: str) -> bool:
    low = html.lower()
    return any(m in low for m in _BLOCK_MARKERS)


# --------------------------------------------------------------------------- #
# Backends
# --------------------------------------------------------------------------- #

def _tavily_search(query: str, limit: int, timeout: int,
                   api_key: str) -> list[SearchHit]:
    """Query the Tavily API and return result hits.

    Raises ``RuntimeError`` on an auth/usage error so the caller can surface it
    (e.g. a bad or exhausted key) instead of silently falling through.
    """
    payload = json.dumps({
        "query": query,
        "max_results": max(1, min(limit, 20)),
        "search_depth": "basic",
    }).encode("utf-8")
    req = urllib.request.Request(
        _TAVILY_ENDPOINT,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": _USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as err:
        detail = ""
        try:
            detail = err.read().decode("utf-8", errors="replace")[:200]
        except Exception:  # noqa: BLE001
            pass
        raise RuntimeError(f"tavily http {err.code}: {detail}".strip()) from err
    except (urllib.error.URLError, OSError) as err:
        raise RuntimeError(f"tavily network error: {err}") from err

    hits: list[SearchHit] = []
    seen: set[str] = set()
    for row in data.get("results", []):
        url = (row.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        key = url.split("#", 1)[0].rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        hits.append(SearchHit(url=url, title=(row.get("title") or "").strip()))
    return hits


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


def _browser_search(query: str, timeout_ms: int = 25000) -> list[SearchHit]:
    """Fallback: run the query in a real headless browser (Bing, then DDG)."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:  # pragma: no cover - import guard
        return []

    encoded = urllib.parse.quote(query)
    targets = (
        (f"https://www.bing.com/search?q={encoded}&setlang=en-us&cc=us",
         "li.b_algo", parse_bing),
        (f"https://html.duckduckgo.com/html/?q={encoded}",
         "a.result__a", parse_results),
    )
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(user_agent=_USER_AGENT, locale="en-US")
        page = context.new_page()
        try:
            for url, ready, parse in targets:
                try:
                    page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                    try:
                        page.wait_for_selector(ready, timeout=8000)
                    except Exception:  # noqa: BLE001 - parse whatever loaded
                        pass
                    hits = parse(page.content())
                except Exception:  # noqa: BLE001 - try the next engine
                    continue
                if hits:
                    return hits
        finally:
            context.close()
            browser.close()
    return []


# --------------------------------------------------------------------------- #
# Disk cache
# --------------------------------------------------------------------------- #

def _cache_path(cache_dir: Path, query: str) -> Path:
    digest = hashlib.sha256(query.lower().encode("utf-8")).hexdigest()[:16]
    return Path(cache_dir) / f"{digest}.json"


def _cache_get(cache_dir: Path | None, query: str,
               max_age_s: int) -> list[SearchHit] | None:
    if not cache_dir:
        return None
    path = _cache_path(cache_dir, query)
    if not path.is_file():
        return None
    if max_age_s and (time.time() - path.stat().st_mtime) > max_age_s:
        return None
    try:
        data = json.loads(path.read_text("utf-8"))
        return [SearchHit(**row) for row in data]
    except Exception:  # noqa: BLE001 - ignore a corrupt cache entry
        return None


def _cache_put(cache_dir: Path | None, query: str, hits: list[SearchHit]) -> None:
    if not cache_dir or not hits:
        return
    path = _cache_path(cache_dir, query)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(json.dumps([asdict(h) for h in hits]), "utf-8")
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #

def web_search(query: str, limit: int = 20, timeout: int = 20,
               cache_dir: str | Path | None = None, use_browser: bool = True,
               cache_max_age_s: int = 7 * 24 * 3600,
               tavily_key: str | None = None) -> list[SearchHit]:
    """Search the web for *query* and return up to *limit* result hits.

    Order: disk cache -> Tavily (if a key is set) -> DuckDuckGo over HTTP ->
    headless-browser fallback. The Tavily key is taken from *tavily_key* or, if
    omitted, the ``TAVILY_API_KEY`` environment variable.
    """
    cache_path_dir = Path(cache_dir) if cache_dir else None
    cached = _cache_get(cache_path_dir, query, cache_max_age_s)
    if cached is not None:
        return cached[:limit]

    hits: list[SearchHit] = []
    key = tavily_key or os.environ.get("TAVILY_API_KEY", "").strip()
    if key:
        try:
            hits = _tavily_search(query, limit, timeout, key)
        except RuntimeError as err:
            print(f"[search] tavily unavailable ({err}); falling back")

    if not hits:
        hits = _ddg_via_http(query, timeout)
    if not hits and use_browser:
        hits = _browser_search(query, timeout_ms=timeout * 1000)

    _cache_put(cache_path_dir, query, hits)
    return hits[:limit]
