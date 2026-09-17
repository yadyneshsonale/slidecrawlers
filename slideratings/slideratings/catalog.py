"""Reconcile a CCR course against the university's official course catalogue.

CCR sometimes renames courses, so the *number* is the reliable key. Given a
course code/number we search the web for the official catalogue entry, confirm
the number appears on a university page, and return the official URL and the
catalogue's course name so the dataset records the canonical title.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from .config import Settings
from .http_util import get_html
from .search import web_search

_TITLE_NOISE = re.compile(
    r"\s*[|\-–—:]\s*(course catalog|catalog|courses|university|college|"
    r"department|academics|home).*$",
    re.IGNORECASE,
)


def _is_university(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return host.endswith(".edu") or host.endswith(".ac.uk") or ".edu." in host


def _page_title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.IGNORECASE | re.DOTALL)
    if m:
        return re.sub(r"<[^>]+>", " ", m.group(1)).strip()
    return ""


def _clean_name(title: str, code: str, number: str) -> str:
    name = _TITLE_NOISE.sub("", title)
    name = re.sub(rf"^\s*{re.escape(code)}\s*[:\-–—]?\s*", "", name, flags=re.I)
    name = re.sub(rf"^\s*[A-Za-z]+\s*0*{re.escape(number)}\s*[:\-–—]?\s*", "",
                  name).strip(" :-–—")
    return name.strip()


def reconcile(settings: Settings, uni_name: str, course_code: str,
              course_number: str) -> dict:
    """Return ``{official_url, official_name, matched}`` for a course.

    ``matched`` is True when a university catalogue page containing the course
    number was found.
    """
    result = {"official_url": None, "official_name": None, "matched": False}
    if not uni_name or not course_number:
        return result
    queries = [
        f"{uni_name} {course_code} course catalog",
        f"{uni_name} {course_code} syllabus",
        f"{uni_name} course catalog {course_number}",
    ]
    number_re = re.compile(rf"(?<!\d){re.escape(course_number)}(?!\d)")
    for query in queries:
        try:
            hits = web_search(
                query,
                limit=settings.slides.max_search_results,
                cache_dir=settings.search_cache_dir,
                use_browser=settings.slides.use_browser_search,
                cache_max_age_s=settings.http.cache_max_age_s,
            )
            hits.sort(key=lambda h: 0 if _is_university(h.url) else 1)
            for hit in hits:
                if not _is_university(hit.url):
                    continue
                html = get_html(hit.url, settings.http, settings.cache_dir)
                if not number_re.search(html):
                    continue
                title = _page_title(html)
                name = _clean_name(title, course_code, course_number) if title else None
                result.update(official_url=hit.url, official_name=name, matched=True)
                return result
        except Exception:  # noqa: BLE001
            continue
    return result
