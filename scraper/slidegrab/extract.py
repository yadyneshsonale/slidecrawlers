"""Discover lecture *slide* links from a page.

Collect downloadable slide decks (PDF/PPT/PPTX) and slide-labelled links while
rejecting notes/readings/homework/solutions. Scans both the static and the
JS-rendered DOM. Can also surface "index" links (lectures/schedule pages) so
the pipeline can follow one hop to find decks.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from config.settings import settings

NUM_RE = re.compile(r"(?:lec(?:ture)?|week|wk|l|w)\s*[-_#]?\s*(\d{1,3})", re.IGNORECASE)
ANY_NUM_RE = re.compile(r"(\d{1,3})")


@dataclass
class SlideCandidate:
    url: str
    text: str
    file_type: str          # pdf|ppt|pptx
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


def _guess_number(text: str, href: str) -> int | None:
    for source in (text, href):
        m = NUM_RE.search(source)
        if m:
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


def is_slide_link(text: str, href: str, cfg=settings.slides) -> tuple[bool, str]:
    hay = f"{text} {href}".lower()
    ext = _ext(href)

    bad = _contains_token(hay, cfg.exclude_tokens)
    if bad:
        return False, f"excluded:{bad}"

    if ext in (".ppt", ".pptx"):
        return True, f"ext:{ext}"

    good = _contains_token(hay, cfg.include_tokens)
    if ext == ".pdf":
        if good:
            return True, f"pdf+token:{good}"
        return False, "pdf-no-slide-token"

    if good:
        return True, f"token:{good}"
    return False, "no-signal"


def _iter_links(html: str, base_url: str):
    soup = BeautifulSoup(html, "lxml")
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"].strip())
        if not href.startswith(("http://", "https://")):
            continue
        text = a.get_text(" ", strip=True)
        yield href, text


def discover_slides(html: str, base_url: str, raw_html: str | None = None,
                    cfg=settings.slides) -> list[SlideCandidate]:
    """Find deck links across the rendered (and optionally raw) DOM."""
    out: list[SlideCandidate] = []
    seen: set[str] = set()
    sources = [html] + ([raw_html] if raw_html and raw_html != html else [])

    for source in sources:
        for href, text in _iter_links(source, base_url):
            if href in seen:
                continue
            ok, reason = is_slide_link(text, href, cfg)
            if not ok:
                continue
            seen.add(href)
            ext = _ext(href)
            out.append(
                SlideCandidate(
                    url=href,
                    text=text,
                    file_type=ext.lstrip("."),
                    lecture_number=_guess_number(text, href),
                    reason=reason,
                )
            )
    return out


def discover_index_links(html: str, base_url: str, cfg=settings.slides) -> list[str]:
    """Same-host links that look like a lectures/schedule page (for one hop)."""
    base_host = urlparse(base_url).netloc.lower()
    out: list[str] = []
    seen: set[str] = set()
    for href, text in _iter_links(html, base_url):
        if href in seen or urlparse(href).netloc.lower() != base_host:
            continue
        hay = f"{text} {href}".lower()
        if _contains_token(hay, cfg.index_tokens):
            seen.add(href)
            out.append(href)
    return out
