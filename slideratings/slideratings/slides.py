"""Slide discovery + download orchestration.

Given a set of search queries (course-based or professor-based), this finds
candidate course pages, follows one hop to lectures/schedule pages when needed
("the tricks"), collects slide-deck links and downloads + verifies them into a
course's ``data/{course_id}`` directory. MIT and known junk/aggregator hosts are
never downloaded from.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from urllib.parse import urlparse

from . import store
from .config import Settings
from .download import download
from .extract import discover_index_links, discover_slides
from .http_util import get_html
from .search import web_search
from .verify import verify_office, verify_pdf

MIT_HOSTS = ("mit.edu", "ocw.mit.edu", "mitocw.com")
JUNK_HOSTS = (
    "youtube.com", "youtu.be", "vimeo.com", "coursehero.com", "studocu.com",
    "scribd.com", "chegg.com", "quizlet.com", "slideshare.net", "academia.edu",
    "researchgate.net", "amazon.com", "docsity.com", "course-notes.org",
    "facebook.com", "twitter.com", "x.com", "linkedin.com", "reddit.com",
)


def is_blocked_host(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(host == h or host.endswith("." + h) for h in MIT_HOSTS + JUNK_HOSTS)


def is_mit(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(host == h or host.endswith("." + h) for h in MIT_HOSTS)


def _next_index(dest_dir: Path) -> int:
    if not dest_dir.exists():
        return 1
    nums = []
    for p in dest_dir.iterdir():
        stem = p.stem
        if stem.isdigit():
            nums.append(int(stem))
    return max(nums) + 1 if nums else 1


def _candidate_decks(settings: Settings, page_url: str):
    """Slide-deck candidates on a page, following one hop to index pages."""
    try:
        html = get_html(page_url, settings.http, settings.cache_dir)
        decks = discover_slides(html, page_url)
        if decks:
            return decks
        out = []
        seen_pages = set()
        for idx_url in discover_index_links(html, page_url)[:settings.slides.max_index_follow]:
            if idx_url in seen_pages or is_blocked_host(idx_url):
                continue
            seen_pages.add(idx_url)
            try:
                idx_html = get_html(idx_url, settings.http, settings.cache_dir)
                out.extend(discover_slides(idx_html, idx_url))
            except Exception:  # noqa: BLE001
                continue
        return out
    except Exception:  # noqa: BLE001
        return []


def discover_and_download(settings: Settings, conn: sqlite3.Connection,
                          course_id: str, queries: list[str],
                          known_sha: set[str]) -> dict:
    """Run *queries*, download slide decks for *course_id*, record everything.

    Returns a small stats dict: pages inspected, decks found/downloaded.
    """
    dest_dir = settings.data_dir / course_id
    number = _next_index(dest_dir)
    seen_pages = set()
    seen_decks = set()
    pages = 0
    found = 0
    downloaded = 0
    for query in queries:
        hits = web_search(
            query,
            limit=settings.slides.max_search_results,
            cache_dir=settings.search_cache_dir,
            use_browser=settings.slides.use_browser_search,
            cache_max_age_s=settings.http.cache_max_age_s,
        )
        for hit in hits:
            if hit.url in seen_pages or is_blocked_host(hit.url):
                continue
            seen_pages.add(hit.url)
            pages += 1
            for cand in _candidate_decks(settings, hit.url):
                key = cand.url.split("#", 1)[0]
                if key in seen_decks or is_blocked_host(cand.url):
                    continue
                seen_decks.add(key)
                found += 1
                if downloaded >= settings.slides.max_decks:
                    continue
                res = download(cand.url, dest_dir, number, settings.http, known_sha)
                store.add_link(conn, course_id, "slide_source", cand.url)
                if not res.ok or not res.file_path or res.skipped:
                    continue
                if res.file_type == "pdf":
                    v = verify_pdf(res.file_path, settings.verify)
                else:
                    v = verify_office(res.file_path)
                if v.valid:
                    store.record_deck(conn, {
                        "course_id": course_id,
                        "source_url": cand.url,
                        "page_url": hit.url,
                        "file_path": res.file_path,
                        "sha256": res.sha256,
                        "file_type": res.file_type,
                        "lecture_number": cand.lecture_number,
                        "status": "verified",
                        "reason": v.reason,
                    })
                    number += 1
                    downloaded += 1
                else:
                    try:
                        Path(res.file_path).unlink()
                    except OSError:
                        pass
                    store.record_deck(conn, {
                        "course_id": course_id,
                        "source_url": cand.url,
                        "page_url": hit.url,
                        "file_path": None,
                        "sha256": res.sha256,
                        "file_type": res.file_type,
                        "lecture_number": cand.lecture_number,
                        "status": "rejected",
                        "reason": v.reason,
                    })
    return {"pages": pages, "found": found, "downloaded": downloaded}
