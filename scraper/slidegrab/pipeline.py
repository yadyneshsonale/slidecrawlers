"""Orchestrate: queries -> web search -> course pages -> decks -> download.

India-first. No URLs are hardcoded; everything is discovered via keyword
search. Pages are fetched with both static and JS-rendered DOM so decks
injected client-side are found. Downloads dedup against the existing dataset.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from config.settings import University, load_topics, load_universities, settings
from slidegrab.coursecodes import discover_course_codes
from slidegrab.download import Downloader, slug
from slidegrab.extract import SlideCandidate, discover_index_links, discover_slides
from slidegrab.fetch import Fetcher
from slidegrab.queries import Query, build_queries
from slidegrab.search import SearchClient, SearchHit, SearchKeyMissing
from slidegrab.store import Store
from slidegrab.verify import verify_pdf

# Hosts that are never real course pages (filter out search noise).
_BLOCK_HOSTS = (
    "youtube.com", "youtu.be", "wikipedia.org", "amazon.", "facebook.com",
    "twitter.com", "x.com", "linkedin.com", "reddit.com", "quora.com",
    "researchgate.net", "slideshare.net", "scribd.com", "coursera.org",
    "udemy.com", "medium.com", "instagram.com",
    # course-notes mills / aggregators (paywalled or junk, no real decks)
    "studocu.com", "coursehero.com", "studocu.", "docsity.com", "askfilo.com",
    "slideserve.com", "slidegeeks.com", "classcentral.com", "intellipaat.com",
    "talentsprint.com", "masaischool.com", "emeritus.org", "cloudxlab.com",
    "ndtv.com", "thebetterindia.com", "springer.com", "link.springer.com",
    "ml.mayyam.com", "infocobuild.com", "freebookcentre.net", "vlabs.ac.in",
    "wordpress.com", "blogspot.com",
)

# Hosts that serve *video* lectures, not downloadable slide files.
_VIDEO_HOSTS = (
    "nptel.ac.in", "onlinecourses.nptel.ac.in", "archive.nptel.ac.in",
    "digimat.in", "swayam.gov.in",
)

_CODE_RE = re.compile(r"\b([A-Z]{2,4}[-\s]?\d{3,5}[A-Z]?)\b")


@dataclass
class CourseStats:
    university: str
    course: str
    page_url: str
    downloaded: int = 0
    skipped: int = 0
    rejected: int = 0


@dataclass
class PipelineStats:
    queries_run: int = 0
    pages_fetched: int = 0
    codes_discovered: int = 0
    courses: list[CourseStats] = field(default_factory=list)

    @property
    def total_downloaded(self) -> int:
        return sum(c.downloaded for c in self.courses)


def _host(url: str) -> str:
    return urlparse(url).netloc.lower()


def is_course_page(hit: SearchHit, uni: University,
                   cfg=settings.course_filter) -> bool:
    host = _host(hit.url)
    if any(b in host for b in _BLOCK_HOSTS):
        return False
    # Video portals don't serve downloadable slide files; only keep a hit there
    # if it points directly at a slide file (rare).
    if any(v in host for v in _VIDEO_HOSTS):
        return hit.url.lower().rsplit("?", 1)[0].endswith((".pdf", ".ppt", ".pptx"))
    # Strongly prefer the university's own domains.
    if any(host == d or host.endswith("." + d) for d in uni.domains):
        return True
    path = urlparse(hit.url).path.lower()
    hay = f"{path} {hit.title} {hit.description}".lower()
    return any(h in hay for h in cfg.course_hints)


def course_name(hit: SearchHit, query: Query) -> str:
    """Derive a stable course slug from the result, preferring a course code."""
    text = f"{hit.url} {hit.title}"
    m = _CODE_RE.search(text)
    if m:
        return slug(m.group(1))
    if query.kind == "code":
        return slug(query.label)
    # Fall back to a meaningful path segment, else the topic.
    segs = [s for s in urlparse(hit.url).path.split("/") if s and s not in ("~",)]
    if segs:
        return slug(segs[-1])
    return slug(query.label)


async def _process_page(
    fetcher: Fetcher,
    downloader: Downloader,
    store: Store,
    uni: University,
    hit: SearchHit,
    query: Query,
    stats: PipelineStats,
    per_course_cap: int,
) -> None:
    # Case 1: the search hit is itself a slide file -> download directly.
    bare = hit.url.lower().rsplit("?", 1)[0]
    if bare.endswith((".pdf", ".ppt", ".pptx")):
        from slidegrab.extract import _guess_number  # local: reuse number parser
        cand = SlideCandidate(
            url=hit.url, text=hit.title, file_type=bare.rsplit(".", 1)[-1],
            lecture_number=_guess_number(hit.title, hit.url), reason="direct-hit",
        )
        cname = course_name(hit, query)
        course_id = store.upsert_course(uni.slug, cname, hit.url)
        cstat = CourseStats(university=uni.slug, course=cname, page_url=hit.url)
        await _download_candidates(
            downloader, store, uni, cname, course_id, [cand], cstat, per_course_cap
        )
        if cstat.downloaded or cstat.skipped:
            stats.courses.append(cstat)
        return

    result = await fetcher.fetch(hit.url)
    if result is None:
        return
    stats.pages_fetched += 1

    candidates = discover_slides(result.html, hit.url, raw_html=result.raw_html)

    # If the landing page has no decks, follow one hop to a lectures/schedule page.
    if not candidates:
        for index_url in discover_index_links(result.html, hit.url)[:3]:
            sub = await fetcher.fetch(index_url)
            if sub is None:
                continue
            stats.pages_fetched += 1
            candidates = discover_slides(sub.html, index_url, raw_html=sub.raw_html)
            if candidates:
                hit = SearchHit(url=index_url, title=hit.title, description=hit.description)
                break
    if not candidates:
        return

    cname = course_name(hit, query)
    course_id = store.upsert_course(uni.slug, cname, hit.url)
    cstat = CourseStats(university=uni.slug, course=cname, page_url=hit.url)
    await _download_candidates(
        downloader, store, uni, cname, course_id, candidates, cstat, per_course_cap
    )
    if cstat.downloaded or cstat.skipped:
        stats.courses.append(cstat)


async def _download_candidates(
    downloader: Downloader,
    store: Store,
    uni: University,
    cname: str,
    course_id: int,
    candidates: list,
    cstat: CourseStats,
    per_course_cap: int,
) -> None:
    for idx, cand in enumerate(candidates[:per_course_cap]):
        res = await downloader.download(
            cand.url, uni.slug, cname, cand.lecture_number, idx
        )
        if not res.ok:
            cstat.rejected += 1
            continue
        if res.skipped:
            cstat.skipped += 1
            continue
        status = "downloaded"
        if res.file_type == "pdf":
            vr = verify_pdf(res.file_path)
            if not vr.valid:
                # Reject text-dense notes / corrupt files.
                try:
                    import os
                    os.remove(res.file_path)
                except OSError:
                    pass
                cstat.rejected += 1
                continue
            status = "verified"
        store.record_deck(
            course_id, res.file_path, res.url, res.sha256,
            res.file_type, cand.lecture_number, status=status,
        )
        cstat.downloaded += 1


async def run(
    region: str = "india",
    limit_unis: int | None = None,
    max_queries_per_uni: int | None = 8,
    max_pages_per_query: int = 4,
    per_course_cap: int = 40,
    dry_run: bool = False,
    discover_codes: bool = True,
    max_code_discovery_queries: int = 3,
    max_codes_per_uni: int = 24,
) -> PipelineStats:
    unis = load_universities()
    if region != "all":
        unis = [u for u in unis if u.region == region]
    if limit_unis:
        unis = unis[:limit_unis]

    topics_cfg = load_topics()
    search = SearchClient()
    store = Store()
    stats = PipelineStats()

    added = store.scan_dataset()
    c, d = store.counts()
    print(f"[store] indexed existing dataset: +{added} files (courses={c}, decks={d})")

    if not search.has_key:
        raise SearchKeyMissing(
            "No search API key found. Set one of TAVILY_API_KEY (free 1k/mo), "
            "SERPER_API_KEY (free 2.5k one-time), or BRAVE_API_KEY. Example:\n"
            "  export TAVILY_API_KEY=your_key_here"
        )
    print(f"[search] provider: {search.provider}")

    async with Fetcher() as fetcher, Downloader() as downloader:
        for uni in unis:
            # Step 1: discover this university's course codes (search-driven).
            codes: list[str] | None = None
            if discover_codes:
                codes = await discover_course_codes(
                    uni, search, topics_cfg, fetcher,
                    max_queries=max_code_discovery_queries,
                    max_codes=max_codes_per_uni,
                )
                stats.codes_discovered += len(codes)
                preview = ", ".join(codes[:12]) + ("..." if len(codes) > 12 else "")
                print(f"\n--- {uni.name}: discovered {len(codes)} course codes "
                      f"[{preview}] ---")

            # Step 2: build slide-deck queries from the discovered codes.
            queries = build_queries(
                uni, topics_cfg, codes=codes, max_per_uni=max_queries_per_uni
            )
            print(f"=== {uni.name} ({uni.region}) -- {len(queries)} queries ===")
            for q in queries:
                try:
                    hits = search.search(q.text)
                except SearchKeyMissing:
                    raise
                except Exception as err:  # noqa: BLE001
                    print(f"  ! search failed: {q.text!r}: {err}")
                    continue
                stats.queries_run += 1
                kept = [h for h in hits if is_course_page(h, uni)][:max_pages_per_query]
                print(f"  [{q.label}] {len(hits)} hits -> {len(kept)} course pages")
                if dry_run:
                    for h in kept:
                        print(f"      {h.url}")
                    continue
                for h in kept:
                    try:
                        await _process_page(
                            fetcher, downloader, store, uni, h, q, stats, per_course_cap
                        )
                    except Exception as err:  # noqa: BLE001
                        print(f"      ! page failed {h.url}: {err}")

    store.close()
    return stats
