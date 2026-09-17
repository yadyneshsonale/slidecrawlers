"""Provider-agnostic web search with on-disk caching and rate limiting.

Keyless web search is blocked from this host, so discovery uses a keyed API.
Supported providers (auto-selected from whichever env var is set):

  - Tavily   (TAVILY_API_KEY)  -- free 1,000 queries/month, recurring
  - Serper   (SERPER_API_KEY)  -- free 2,500 queries, one-time (Google results)
  - Brave    (BRAVE_API_KEY)   -- if you still have a key

Force one with SEARCH_PROVIDER=tavily|serper|brave. Results are cached on disk
so re-runs and quota-limited free tiers are respected.
"""
from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from config.settings import settings


class SearchKeyMissing(RuntimeError):
    """Raised when a live search is attempted without any provider key."""


# Back-compat alias (older code/tests imported BraveKeyMissing).
BraveKeyMissing = SearchKeyMissing


@dataclass
class SearchHit:
    url: str
    title: str
    description: str


def _cache_key(provider: str, query: str, count: int) -> str:
    raw = f"{provider}|{query}|{count}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _cache_path(provider: str, query: str, count: int):
    settings.search_cache_dir.mkdir(parents=True, exist_ok=True)
    return settings.search_cache_dir / f"{_cache_key(provider, query, count)}.json"


def _post_json(url: str, headers: dict, body: dict, timeout: int) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get_json(url: str, headers: dict, timeout: int) -> dict:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---- per-provider request builders / parsers ----

def _call_tavily(key: str, query: str, count: int, timeout: int) -> list[SearchHit]:
    payload = _post_json(
        "https://api.tavily.com/search",
        {"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        {"query": query, "max_results": count, "search_depth": "basic"},
        timeout,
    )
    out: list[SearchHit] = []
    for r in payload.get("results", []) or []:
        url = r.get("url")
        if url:
            out.append(SearchHit(url, r.get("title", ""), r.get("content", "")))
    return out


def _call_serper(key: str, query: str, count: int, timeout: int) -> list[SearchHit]:
    payload = _post_json(
        "https://google.serper.dev/search",
        {"Content-Type": "application/json", "X-API-KEY": key},
        {"q": query, "num": count},
        timeout,
    )
    out: list[SearchHit] = []
    for r in payload.get("organic", []) or []:
        url = r.get("link")
        if url:
            out.append(SearchHit(url, r.get("title", ""), r.get("snippet", "")))
    return out


def _call_brave(key: str, query: str, count: int, timeout: int) -> list[SearchHit]:
    params = urllib.parse.urlencode({"q": query, "count": count})
    payload = _get_json(
        f"https://api.search.brave.com/res/v1/web/search?{params}",
        {"Accept": "application/json", "Accept-Encoding": "identity",
         "X-Subscription-Token": key},
        timeout,
    )
    out: list[SearchHit] = []
    for r in (payload.get("web") or {}).get("results", []) or []:
        url = r.get("url")
        if url:
            out.append(SearchHit(url, r.get("title", ""), r.get("description", "")))
    return out


_PROVIDERS = {
    "tavily": _call_tavily,
    "serper": _call_serper,
    "brave": _call_brave,
}


class SearchClient:
    """Synchronous multi-provider search client.

    Usage:
        sc = SearchClient()
        hits = sc.search("IIT Madras CS7015 deep learning lecture slides")
    """

    def __init__(self, cfg=settings.search) -> None:
        self.cfg = cfg
        self.provider = cfg.active_provider()
        self._last_call = 0.0

    @property
    def has_key(self) -> bool:
        return self.provider is not None

    def _throttle(self) -> None:
        gap = time.monotonic() - self._last_call
        if gap < self.cfg.min_seconds_between_calls:
            time.sleep(self.cfg.min_seconds_between_calls - gap)
        self._last_call = time.monotonic()

    def _read_cache(self, query: str, count: int) -> list[SearchHit] | None:
        path = _cache_path(self.provider, query, count)
        if not path.exists():
            return None
        try:
            return [SearchHit(**h) for h in json.loads(path.read_text())]
        except Exception:  # noqa: BLE001 - corrupt cache => re-fetch
            return None

    def _write_cache(self, query: str, count: int, hits: list[SearchHit]) -> None:
        _cache_path(self.provider, query, count).write_text(
            json.dumps([h.__dict__ for h in hits], indent=0)
        )

    def _call_api(self, query: str, count: int) -> list[SearchHit]:
        if not self.provider:
            raise SearchKeyMissing(
                "No search API key found. Set one of TAVILY_API_KEY (free 1k/mo), "
                "SERPER_API_KEY (free 2.5k one-time), or BRAVE_API_KEY. Example:\n"
                "  export TAVILY_API_KEY=your_key_here"
            )
        fn = _PROVIDERS[self.provider]
        key = self.cfg.key_for(self.provider)
        last_err: Exception | None = None
        for attempt in range(self.cfg.max_retries + 1):
            self._throttle()
            try:
                return fn(key, query, count, self.cfg.timeout)
            except urllib.error.HTTPError as err:
                last_err = err
                if err.code == 429:  # rate limited -> exponential backoff
                    time.sleep(2.0 * (attempt + 1))
                    continue
                if 500 <= err.code < 600:
                    time.sleep(1.0 * (attempt + 1))
                    continue
                raise
            except (urllib.error.URLError, TimeoutError) as err:
                last_err = err
                time.sleep(1.0 * (attempt + 1))
        if last_err:
            raise last_err
        return []

    def search(self, query: str, count: int | None = None) -> list[SearchHit]:
        count = count or self.cfg.results_per_query
        cached = self._read_cache(query, count)
        if cached is not None:
            return cached
        hits = self._call_api(query, count)
        self._write_cache(query, count, hits)
        return hits


# Back-compat alias (older code/tests imported BraveSearch).
BraveSearch = SearchClient
