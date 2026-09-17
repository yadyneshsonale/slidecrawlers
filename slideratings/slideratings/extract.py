"""Discover lecture *slide* links from a page (PDF / PPT / PPTX).

Collects downloadable decks and slide-labelled links while rejecting
notes/readings/homework/solutions. Also surfaces same-host "index" links
(lectures/schedule pages) so the pipeline can follow one hop to find decks.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

INCLUDE_TOKENS = (
    "slide", "slides", "lecture", "lectures", "deck",
    "presentation", "chapter", "week", "lec", "talk",
)
EXCLUDE_TOKENS = (
    "notes", "note", "homework", "hw", "solution", "solutions",
    "assignment", "assignments", "exam", "midterm", "final", "quiz",
    "syllabus", "reading", "readings", "problem", "pset", "handout",
    "rubric", "project", "lab", "cheatsheet", "transcript",
)
INDEX_TOKENS = (
    "lecture", "lectures", "schedule", "syllabus", "calendar",
    "slides", "materials", "course", "topics",
)
NUM_RE = re.compile(
    r"(?:lec(?:ture)?|week|wk|chapter|ch|l|w)\s*[-_#]?\s*(\d{1,3})",
    re.IGNORECASE,
)
ANY_NUM_RE = re.compile(r"(\d{1,3})")


@dataclass
class SlideCandidate:
    url: str
    text: str
    file_type: str
    lecture_number: int | None
    reason: str


def _ext(url: str) -> str:
    path = urlparse(url).path.lower()
    for e in (".pptx", ".ppt", ".pdf"):
        if path.endswith(e):
            return e
    return ""


def _contains_token(haystack: str, tokens) -> str | None:
    for t in tokens:
        if re.search(rf"(?<![a-z]){re.escape(t)}(?![a-z])", haystack):
            return t
    return None


def guess_number(text: str, href: str) -> int | None:
    for source in (text, href):
        if not source:
            continue
        m = NUM_RE.search(source)
        if not m:
            continue
        n = int(m.group(1))
        if 1 <= n <= 99:
            return n
    fname = urlparse(href).path.rsplit("/", 1)[-1]
    m = ANY_NUM_RE.search(fname)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 99:
            return n
    return None


def is_slide_link(text: str, href: str) -> tuple[bool, str]:
    hay = f"{text} {href}".lower()
    ext = _ext(href)
    bad = _contains_token(hay, EXCLUDE_TOKENS)
    good = _contains_token(hay, INCLUDE_TOKENS)
    if ext in (".ppt", ".pptx"):
        if bad and not good:
            return (False, f"excluded:{bad}")
        return (True, f"ext:{ext}")
    if ext == ".pdf":
        if good and not bad:
            return (True, f"pdf+token:{good}")
        if bad:
            return (False, f"excluded:{bad}")
        return (False, "no-signal")
    return (False, "no-signal")


def _iter_links(html: str, base_url: str):
    soup = BeautifulSoup(html, "lxml")
    for a in soup.select("a[href]"):
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("javascript:", "mailto:", "#")):
            continue
        full = urljoin(base_url, href)
        if not full.startswith(("http://", "https://")):
            continue
        text = a.get_text(" ", strip=True)
        yield full, text


def discover_slides(html: str, base_url: str) -> list[SlideCandidate]:
    """Find deck links in the page DOM."""
    out: list[SlideCandidate] = []
    seen: set[str] = set()
    for href, text in _iter_links(html, base_url):
        key = href.split("#", 1)[0]
        if key in seen:
            continue
        ok, reason = is_slide_link(text, href)
        if not ok:
            continue
        seen.add(key)
        ext = _ext(href)
        out.append(SlideCandidate(
            url=href,
            text=text,
            file_type=ext.lstrip("."),
            lecture_number=guess_number(text, href),
            reason=reason,
        ))
    return out


def discover_index_links(html: str, base_url: str) -> list[str]:
    """Same-host links that look like a lectures/schedule page (for one hop)."""
    base_host = urlparse(base_url).netloc.lower()
    out: list[str] = []
    seen: set[str] = set()
    for href, text in _iter_links(html, base_url):
        key = href.split("#", 1)[0]
        if key in seen or urlparse(href).netloc.lower() != base_host:
            continue
        if _ext(href):
            continue
        hay = f"{text} {href}".lower()
        if not _contains_token(hay, INDEX_TOKENS):
            continue
        seen.add(key)
        out.append(href)
    return out
