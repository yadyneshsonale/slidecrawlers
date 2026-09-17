#!/usr/bin/env python3
"""For every ``course_college`` string in ccr_courses.db, run a web search for
``"<course_college> course slides"`` and store the first 10 result links.

Each output row holds the ``course_college`` string plus up to 10 result links
(``link_1`` .. ``link_10``) and a JSON copy of the same links.

Search backend
--------------
Google's web results are served as a JavaScript-only page (no parseable links
without an API key or a headless browser), so this uses DuckDuckGo's HTML
endpoints, which return the same kind of organic web results. It rotates user
agents, falls back to the ``lite`` endpoint, and backs off on rate-limit (HTTP
202) responses.

Usage:
    python3.11 ccr_slide_search.py                 # full run (resumes)
    python3.11 ccr_slide_search.py --limit 20      # only first 20 rows
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sqlite3
import subprocess
import time
import urllib.parse
from datetime import datetime, timezone

import requests

HTML_ENDPOINT = "https://html.duckduckgo.com/html/"
LITE_ENDPOINT = "https://lite.duckduckgo.com/lite/"
NUM_LINKS = 10

# --------------------------------------------------------------------------- #
# Tavily -- an LLM/agent search API (https://tavily.com). Unlike scraping
# Brave/DDG/Startpage (which get rate-limited / CAPTCHA-blocked from server
# IPs), this is a keyed HTTPS API, so it keeps working. Set TAVILY_API_KEY in
# the environment to enable it; it is then tried first.
# --------------------------------------------------------------------------- #
TAVILY_ENDPOINT = "https://api.tavily.com/search"
SERPAPI_ENDPOINT = "https://serpapi.com/search.json"


class QuotaExhausted(Exception):
    """Raised when a keyed search API has run out of credits for the period.

    Lets the caller stop using that backend (and pause the whole run when every
    configured API is exhausted) instead of silently returning no results.
    """

    def __init__(self, provider: str):
        super().__init__(f"{provider} quota exhausted")
        self.provider = provider


def tavily_search(session: requests.Session, query: str, *,
                  api_key: str | None = None, max_results: int = NUM_LINKS,
                  timeout: int = 20, max_attempts: int = 3) -> list[str]:
    """Return organic result URLs from the Tavily search API (keyed, no scraping).

    Returns ``[]`` when no ``TAVILY_API_KEY`` is configured, so callers can fall
    back to the other backends. Raises :class:`QuotaExhausted` when the plan's
    credits are spent.
    """
    api_key = api_key or os.environ.get("TAVILY_API_KEY")
    if not api_key:
        return []
    payload = {"api_key": api_key, "query": query,
               "max_results": max_results, "search_depth": "basic"}
    for attempt in range(max_attempts):
        try:
            resp = session.post(TAVILY_ENDPOINT, json=payload, timeout=timeout)
        except requests.RequestException:
            time.sleep(2 * (attempt + 1) + random.uniform(0, 2))
            continue
        if resp.status_code == 200:
            try:
                data = resp.json()
            except ValueError:
                return []
            seen: set[str] = set()
            out: list[str] = []
            for item in data.get("results", []):
                url = (item.get("url") or "").split("#", 1)[0]
                if url and url not in seen:
                    seen.add(url)
                    out.append(url)
            return out[:max_results]
        # 432/433 = plan/usage limit; a persistent 429 also means out of credits.
        if resp.status_code in (402, 432, 433):
            raise QuotaExhausted("tavily")
        if resp.status_code == 429:
            if attempt >= max_attempts - 1:
                raise QuotaExhausted("tavily")
            time.sleep(5 * (attempt + 1) + random.uniform(0, 3))
            continue
        return []  # 400/401 (bad request / key) -> give up, let caller fall back
    return []


def serpapi_search(session: requests.Session, query: str, *,
                   api_key: str | None = None, max_results: int = NUM_LINKS,
                   engine: str = "google", timeout: int = 20,
                   max_attempts: int = 3) -> list[str]:
    """Return organic result URLs from SerpAPI (keyed, no scraping).

    Reads ``SERPAPI_API_KEY`` (or ``SERPAPI_KEY``). Returns ``[]`` when no key is
    set; raises :class:`QuotaExhausted` when the account is out of searches.
    """
    api_key = (api_key or os.environ.get("SERPAPI_API_KEY")
               or os.environ.get("SERPAPI_KEY"))
    if not api_key:
        return []
    params = {"engine": engine, "q": query, "num": max_results,
              "api_key": api_key}
    for attempt in range(max_attempts):
        try:
            resp = session.get(SERPAPI_ENDPOINT, params=params, timeout=timeout)
        except requests.RequestException:
            time.sleep(2 * (attempt + 1) + random.uniform(0, 2))
            continue
        try:
            data = resp.json()
        except ValueError:
            data = {}
        err = (data.get("error") or "").lower()
        if err and ("run out of searches" in err or "run out of" in err
                    or "exceeded" in err or "no longer available" in err):
            raise QuotaExhausted("serpapi")
        if resp.status_code == 401 or "invalid api key" in err:
            return []  # bad key -> let caller fall back
        if resp.status_code == 429:
            if attempt >= max_attempts - 1:
                raise QuotaExhausted("serpapi")
            time.sleep(5 * (attempt + 1) + random.uniform(0, 3))
            continue
        if resp.status_code == 200:
            seen: set[str] = set()
            out: list[str] = []
            for item in data.get("organic_results", []):
                url = (item.get("link") or "").split("#", 1)[0]
                if url and url not in seen:
                    seen.add(url)
                    out.append(url)
            return out[:max_results]
        time.sleep(2 * (attempt + 1) + random.uniform(0, 2))
    return []

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/16.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
]

_HTML_RE = re.compile(r'<a[^>]*class="result__a"[^>]*href="([^"]+)"')
_LITE_RE = re.compile(r'<a[^>]*class="result-link"[^>]*href="([^"]+)"')


def _decode(href: str) -> str:
    """Resolve a DuckDuckGo redirect href to the underlying target URL."""
    if href.startswith("//"):
        href = "https:" + href
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
    if "uddg" in qs:
        return urllib.parse.unquote(qs["uddg"][0])
    return href


def _extract(text: str, pattern: re.Pattern) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for href in pattern.findall(text):
        url = _decode(href)
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def search(session: requests.Session, query: str, *, max_attempts: int = 6) -> list[str]:
    """Return up to ``NUM_LINKS`` result URLs, retrying through rate limits."""
    for attempt in range(max_attempts):
        ua = random.choice(USER_AGENTS)
        headers = {"User-Agent": ua, "Accept-Language": "en-US,en;q=0.9"}
        for endpoint, pattern in ((HTML_ENDPOINT, _HTML_RE), (LITE_ENDPOINT, _LITE_RE)):
            try:
                resp = session.post(endpoint, data={"q": query},
                                    headers=headers, timeout=25)
            except requests.RequestException:
                continue
            if resp.status_code == 200:
                links = _extract(resp.text, pattern)
                if links:
                    return links[:NUM_LINKS]
        # Rate limited (202) or empty: wait with exponential-ish backoff.
        time.sleep(5 * (attempt + 1) + random.uniform(0, 3))
    return []


# --------------------------------------------------------------------------- #
# Brave Search backend (free, no API key, reachable when DuckDuckGo is not).
# Brave returns real organic results as plain ``<a href="https://...">`` links,
# unlike Google/Bing which serve a JavaScript-only shell.
# --------------------------------------------------------------------------- #

BRAVE_ENDPOINT = "https://search.brave.com/search"
_BRAVE_HREF_RE = re.compile(r'href="(https?://[^"]+)"')
_BRAVE_SKIP = (
    "brave.com", "bravesoftware", "search.brave", "gstatic", "duckduckgo",
    "google.", "bing.com", "microsoft",
)


def brave_search(session: requests.Session, query: str,
                 *, max_attempts: int = 3, timeout: int = 12) -> list[str]:
    """Return organic result URLs from Brave Search (no API key required)."""
    for attempt in range(max_attempts):
        ua = random.choice(USER_AGENTS)
        headers = {"User-Agent": ua, "Accept-Language": "en-US,en;q=0.9"}
        try:
            resp = session.get(BRAVE_ENDPOINT, params={"q": query, "source": "web"},
                               headers=headers, timeout=timeout)
        except requests.RequestException:
            time.sleep(2 * (attempt + 1) + random.uniform(0, 2))
            continue
        if resp.status_code == 200:
            seen: set[str] = set()
            out: list[str] = []
            for href in _BRAVE_HREF_RE.findall(resp.text):
                host = urllib.parse.urlsplit(href).netloc.lower()
                if any(s in host for s in _BRAVE_SKIP):
                    continue
                url = href.split("#", 1)[0]
                if url not in seen:
                    seen.add(url)
                    out.append(url)
            if out:
                return out[:NUM_LINKS]
        # HTTP 429 = rate limited by IP; needs a long cooldown, not a quick retry.
        if resp.status_code == 429:
            time.sleep(30 * (attempt + 1) + random.uniform(0, 10))
        else:
            time.sleep(3 * (attempt + 1) + random.uniform(0, 2))
    return []


# --------------------------------------------------------------------------- #
# Self-hosted SearXNG backend (free, no API key, no external rate limit).
# Aggregates Google/Bing/Brave/etc. and returns JSON. Run locally with:
#   docker run -d --name searxng -p 8080:8080 -v ~/searxng:/etc/searxng \
#       searxng/searxng:latest
# and enable ``formats: [html, json]`` + ``limiter: false`` in settings.yml.
# --------------------------------------------------------------------------- #

SEARXNG_ENDPOINT = "http://localhost:8080/search"
# Optionally pin SearXNG to a specific engine (e.g. "bing"). Empty = the
# instance default. NOTE: on this host every general engine is currently
# rate-limited/CAPTCHA-blocked and Bing returns irrelevant results, so leaving
# this empty (and supplying a real search API key) is preferred.
SEARXNG_ENGINES = ""


def searxng_search(session: requests.Session, query: str,
                   *, endpoint: str = SEARXNG_ENDPOINT,
                   engines: str = SEARXNG_ENGINES,
                   max_attempts: int = 3, timeout: int = 20) -> list[str]:
    """Return organic result URLs from a local SearXNG instance (JSON)."""
    headers = {"User-Agent": random.choice(USER_AGENTS),
               "Accept": "application/json"}
    params = {"q": query, "format": "json"}
    if engines:
        params["engines"] = engines
    for attempt in range(max_attempts):
        try:
            resp = session.get(endpoint,
                               params=params,
                               headers=headers, timeout=timeout)
        except requests.RequestException:
            time.sleep(2 * (attempt + 1) + random.uniform(0, 2))
            continue
        if resp.status_code == 200:
            try:
                data = resp.json()
            except ValueError:
                data = {}
            seen: set[str] = set()
            out: list[str] = []
            for item in data.get("results", []):
                url = (item.get("url") or "").split("#", 1)[0]
                if url and url not in seen:
                    seen.add(url)
                    out.append(url)
            if out:
                return out[:NUM_LINKS]
            return []
        time.sleep(3 * (attempt + 1) + random.uniform(0, 2))
    return []


def init_db(conn: sqlite3.Connection) -> None:
    cols = ", ".join(f"link_{i} TEXT" for i in range(1, NUM_LINKS + 1))
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS slide_links (
            course_college TEXT PRIMARY KEY,
            num_links      INTEGER,
            {cols},
            links_json     TEXT,
            searched_at    TEXT
        )
        """
    )
    conn.commit()


def load_targets(courses_db: str) -> list[str]:
    conn = sqlite3.connect(courses_db)
    rows = [r[0] for r in conn.execute(
        "SELECT DISTINCT course_college FROM courses "
        "WHERE course_college IS NOT NULL ORDER BY course_college"
    )]
    conn.close()
    return rows


def crawl_running() -> bool:
    """True while the CCR course crawl process is still alive."""
    return subprocess.run(
        ["pgrep", "-f", "ccr_course_index.py"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode == 0


def run_pass(out: sqlite3.Connection, session: requests.Session,
             args, done: set[str], cols: str, placeholders: str) -> int:
    """Search every not-yet-done course currently in the courses DB.

    Returns the number of rows that successfully found links this pass.
    """
    targets = load_targets(args.courses_db)
    if args.limit is not None:
        targets = targets[: args.limit]
    todo = [t for t in targets if t not in done]
    print(f"pass: {len(targets)} course rows, {len(done)} done, "
          f"{len(todo)} to search", flush=True)

    found = 0
    for idx, course_college in enumerate(todo, 1):
        query = f"{course_college} course slides"
        links = search(session, query)
        padded = (links + [None] * NUM_LINKS)[:NUM_LINKS]
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        out.execute(
            f"INSERT OR REPLACE INTO slide_links "
            f"(course_college, num_links, {cols}, links_json, searched_at) "
            f"VALUES ({placeholders})",
            [course_college, len(links), *padded, json.dumps(links), now],
        )
        out.commit()
        # Only mark done when links were found, so transient 0-link rows retry.
        if links:
            done.add(course_college)
            found += 1
        print(f"[{idx}/{len(todo)}] {course_college!r}: {len(links)} links",
              flush=True)
        time.sleep(args.delay + random.uniform(0, args.delay))
    return found


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--courses-db", default="ccr_courses.db")
    ap.add_argument("--out-db", default="ccr_slide_links.db")
    ap.add_argument("--delay", type=float, default=2.0,
                    help="base seconds to wait between queries")
    ap.add_argument("--limit", type=int, default=None,
                    help="only process the first N course rows")
    ap.add_argument("--follow", action="store_true",
                    help="keep polling the courses DB for new rows while the "
                         "crawl (ccr_course_index.py) is still running")
    ap.add_argument("--poll", type=float, default=60.0,
                    help="seconds to wait between passes in --follow mode")
    args = ap.parse_args()

    out = sqlite3.connect(args.out_db)
    init_db(out)
    # Treat a row as done only if it found links, so 0-link rows (usually a
    # transient rate-limit) are retried on the next run.
    done = {r[0] for r in out.execute(
        "SELECT course_college FROM slide_links WHERE num_links > 0")}

    session = requests.Session()
    placeholders = ", ".join(["?"] * (NUM_LINKS + 4))
    cols = ", ".join(f"link_{i}" for i in range(1, NUM_LINKS + 1))

    if args.follow:
        while True:
            run_pass(out, session, args, done, cols, placeholders)
            if not crawl_running():
                # Crawl is over; one last catch-up pass for final inserts.
                run_pass(out, session, args, done, cols, placeholders)
                break
            print(f"crawl still running; sleeping {args.poll:.0f}s before next "
                  f"pass", flush=True)
            time.sleep(args.poll)
    else:
        run_pass(out, session, args, done, cols, placeholders)

    total = out.execute("SELECT COUNT(*) FROM slide_links").fetchone()[0]
    out.close()
    print(f"\ndone; {total} rows in {args.out_db}")


if __name__ == "__main__":
    main()
