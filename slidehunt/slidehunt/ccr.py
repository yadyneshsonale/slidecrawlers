"""CollegeClassReviews parsing (course-page ratings).

CCR is a Next.js site whose data is server-rendered into the HTML (both as
visible markup and as React Server Component JSON). slidehunt only needs to read
a single course page's rating metrics, given a university slug + a course code.
CCR covers a limited set of universities, so lookups are best-effort.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

from .config import Settings
from .http_util import get_html

METRICS = (
    ("student_satisfaction", "Student Satisfaction"),
    ("challenge_level", "Challenge Level"),
    ("grade_accessibility", "Grade Accessibility"),
    ("time_investment", "Time Investment"),
    ("attendance_importance", "Attendance Importance"),
    ("recommendation_rate", "Recommendation Rate"),
)
_SLUG_SPLIT = re.compile(r"^([a-z]+)[\s\-]?([0-9][0-9a-z]*)$", re.IGNORECASE)


def _deescape(html: str) -> str:
    r"""Turn RSC-escaped JSON (``\"``) back into plain quotes for regexing."""
    return html.replace('\\"', '"')


def split_code(slug: str) -> tuple[str, str]:
    """Split a course slug into (course_code, course_number)."""
    m = _SLUG_SPLIT.match(slug.strip())
    if not m:
        return (slug.upper(), re.sub(r"\D", "", slug))
    digits = m.group(2)
    letters = m.group(1).upper()
    number = re.sub(r"\D", "", slug)
    return (f"{letters} {digits}", number)


def _jsonld_blocks(html: str) -> list:
    out: list = []
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all("script", type="application/ld+json"):
        if not tag.string:
            continue
        try:
            data = json.loads(tag.string)
        except Exception:  # noqa: BLE001
            continue
        out.extend(data if isinstance(data, list) else [data])
    return out


def _metric_value(text: str, label: str) -> float | None:
    """Read a 0-1 metric for *label* from the nearest width percentage."""
    for m in re.finditer(re.escape(label), text):
        window = text[m.start():m.start() + 400]
        wm = re.search(r'width["\s:]+(\d+)%', window)
        if not wm:
            continue
        return int(wm.group(1)) / 100
    return None


def parse_course_page(html: str, slug: str) -> dict:
    text = _deescape(html)
    soup = BeautifulSoup(html, "lxml")
    code, number = split_code(slug)
    name = ""
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(" ", strip=True)
        parts = re.split(r"\s*[—:–-]\s*", title, maxsplit=1)
        if len(parts) == 2 and re.search(r"\d", parts[0]):
            name = parts[1].strip()
        else:
            name = title

    star_rating = None
    num_reviews = None
    try:
        for block in _jsonld_blocks(html):
            agg = block.get("aggregateRating") if isinstance(block, dict) else None
            if not isinstance(agg, dict):
                continue
            star_rating = float(agg.get("ratingValue"))
            num_reviews = int(agg.get("reviewCount") or agg.get("ratingCount"))
            break
    except (TypeError, ValueError):
        pass
    if num_reviews is None:
        m = re.search(r"Aggregated from\s+([0-9,]+)\s+student rating", text)
        if m:
            num_reviews = int(m.group(1).replace(",", ""))

    difficulty = None
    m = re.search(r"Difficulty:[^0-9]{0,40}?([0-9]+(?:\.[0-9]+)?)\s*/\s*5", text)
    if m:
        difficulty = float(m.group(1))

    professors: list[str] = []
    for pm in re.finditer(r"Professor:</span><span[^>]*>([^<]+)</span>", text):
        nm = pm.group(1).strip()
        if not nm or nm in professors:
            continue
        professors.append(nm)

    row = {
        "course_code": code,
        "course_number": number,
        "course_name": name,
        "star_rating": star_rating,
        "num_reviews": num_reviews,
        "difficulty": difficulty,
        "professors": professors,
        "has_ratings": star_rating is not None or num_reviews is not None,
    }
    for col, label in METRICS:
        row[col] = _metric_value(text, label)
    return row


def course_slug_variants(code: str) -> list[str]:
    """Candidate CCR course slugs for a raw code like ``cs223``.

    ``cs223`` -> ['cs223', 'cs-223']; ``CS 223`` -> ['cs223', 'cs-223'].
    """
    norm = re.sub(r"[^a-z0-9]", "", code.lower())
    out = [norm]
    m = re.match(r"^([a-z]+)([0-9].*)$", norm)
    if m:
        dashed = f"{m.group(1)}-{m.group(2)}"
        if dashed not in out:
            out.append(dashed)
    return out


def fetch_course(settings: Settings, uni_slug: str, code: str,
                 use_cache: bool = True) -> dict | None:
    """Best-effort CCR course-rating lookup for *code* at *uni_slug*.

    Tries a couple of slug spellings and returns the first page that actually
    carries ratings, else ``None``.
    """
    for slug in course_slug_variants(code):
        url = f"{settings.ccr.base}/universities/{uni_slug}/courses/{slug}"
        try:
            html = get_html(url, settings.http, settings.cache_dir, use_cache=use_cache)
        except Exception:  # noqa: BLE001
            continue
        if not html:
            continue
        row = parse_course_page(html, slug)
        if row.get("has_ratings"):
            row["ccr_url"] = url
            return row
    return None
