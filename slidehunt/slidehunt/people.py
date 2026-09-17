"""Extract instructor / professor names from a course page.

Best-effort: scans the page text for common "Instructor:" / "Professor:" /
"Taught by" patterns and validates each candidate through ``rmp.normalize_name``
so only plausible person names survive.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from .rmp import normalize_name

# Labels that typically precede an instructor's name on a course page.
_LABEL_RE = re.compile(
    r"(?:instructors?|professors?|lecturers?|taught\s+by|"
    r"course\s+staff|teaching\s+staff|faculty)\s*[:\-\u2013\u2014]\s*"
    r"(?P<names>[^\n\r|]{2,160})",
    re.IGNORECASE,
)
# Split a captured run into individual names.
_SPLIT_RE = re.compile(r"\s*(?:,|;|/|\band\b|&|\|)\s*", re.IGNORECASE)
# Drop trailing role/department noise after a name.
_NOISE_RE = re.compile(
    r"\b(office|email|e-mail|hours|room|phone|ta|tas|department|"
    r"lecture|section|mw|tth|http)\b.*$",
    re.IGNORECASE,
)


def _clean_fragment(piece: str) -> str:
    piece = _NOISE_RE.sub("", piece)
    piece = re.sub(r"\s+", " ", piece).strip(" ,.-\t")
    return piece


def extract_instructors(html: str, title: str = "", limit: int = 6) -> list[str]:
    """Return a de-duplicated list of plausible instructor names from *html*."""
    if not html:
        return []
    text = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
    hay = f"{title}\n{text}"

    out: list[str] = []
    seen: set[str] = set()
    for m in _LABEL_RE.finditer(hay):
        run = m.group("names")
        for frag in _SPLIT_RE.split(run):
            frag = _clean_fragment(frag)
            if not frag:
                continue
            name = normalize_name(frag)
            if not name:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(name)
            if len(out) >= limit:
                return out
    return out
