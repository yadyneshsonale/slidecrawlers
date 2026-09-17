"""CollegeClassReviews (CCR) course-rating lookup.

Given a CCR university slug and a course code, probe the site for a real course
page and return its crowdsourced metrics. Mirrors the manual workflow:

    browse the university -> look for the course number -> only accept a code
    whose department abbreviation belongs to the target discipline -> otherwise
    record "not found".

CCR returns HTTP 200 even for course slugs that do not exist, so existence is
decided by whether the page actually contains rating data (the same ``width:N%``
metric bars that ccr_course_ratings.parse extracts), not by status code.
"""
from __future__ import annotations

import urllib.error
from dataclasses import dataclass, field

from ccr_course_ratings import BASE, fetch, parse
from school_map import CourseCode, sibling_abbreviations


@dataclass
class CCRResult:
    status: str                     # 'found' | 'not_found' | 'no_school' | 'error'
    slug: str | None = None         # university slug used
    code_used: str | None = None    # course code that matched, e.g. 'cse127'
    url: str | None = None
    tried: list[str] = field(default_factory=list)   # codes probed
    metrics: dict = field(default_factory=dict)      # parsed rating fields
    error: str | None = None


def _has_ratings(metrics: dict) -> bool:
    """A CCR page is a real, rated course if any metric / star value is present."""
    if metrics.get("star_rating") is not None:
        return True
    if metrics.get("num_reviews"):
        return True
    return any(
        metrics.get(k) is not None
        for k in (
            "student_satisfaction", "challenge_level", "grade_accessibility",
            "time_investment", "attendance_importance", "recommendation_rate",
        )
    )


def lookup(ccr_slug: str | None, code: CourseCode) -> CCRResult:
    """Look up a course on CCR, trying discipline-sibling abbreviations.

    Returns the first code that resolves to a page with real rating data. If no
    sibling matches, the result is ``not_found`` (the human's "enter not found").
    """
    if not ccr_slug:
        return CCRResult(status="no_school", code_used=code.ccr_code)

    candidates: list[str]
    if code.prefix:
        candidates = [f"{abbr}{code.number}" for abbr in sibling_abbreviations(code)]
    else:
        # MIT-style numeric code: only the exact form is meaningful.
        candidates = [code.number.replace(".", "")]

    tried: list[str] = []
    for cand in candidates:
        cand = cand.lower()
        if cand in tried:
            continue
        tried.append(cand)
        url = f"{BASE}{ccr_slug}/courses/{cand}"
        try:
            html = fetch(url)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                continue
            return CCRResult(status="error", slug=ccr_slug, tried=tried,
                             error=f"http {exc.code}", code_used=cand, url=url)
        except (urllib.error.URLError, OSError) as exc:
            return CCRResult(status="error", slug=ccr_slug, tried=tried,
                             error=str(exc), code_used=cand, url=url)

        metrics = parse(html)
        if _has_ratings(metrics):
            return CCRResult(status="found", slug=ccr_slug, code_used=cand,
                             url=url, tried=tried, metrics=metrics)

    return CCRResult(status="not_found", slug=ccr_slug,
                     code_used=code.ccr_code, tried=tried)
