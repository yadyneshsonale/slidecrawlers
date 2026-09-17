"""Map a CollegeClassReviews university slug to a short dataset prefix.

The prefixes mirror the existing ``RateMySlides/data/{uni}-{coursenum}`` folders
so that newly scraped courses line up with the established naming convention.
Unknown universities get a deterministic, compact prefix derived from the slug.
"""
from __future__ import annotations

import re

KNOWN: dict[str, str] = {
    "carnegie-mellon-university": "cmu",
    "cornell-university": "cornell",
    "georgia-tech": "gatech",
    "georgia-institute-of-technology": "gatech",
    "massachusetts-institute-of-technology": "mit",
    "stanford-university": "stanford",
    "princeton-university": "princeton",
    "uc-berkeley": "ucb",
    "university-of-california-berkeley": "ucb",
    "uc-santa-barbara": "ucsb",
    "university-of-california-santa-barbara": "ucsb",
    "university-of-illinois-urbana-champaign": "uiuc",
    "university-of-illinois-at-urbana-champaign": "uiuc",
    "university-of-michigan": "umich",
    "university-of-minnesota-twin-cities": "umn",
    "university-of-arizona": "uoarizona",
    "university-of-toronto": "uot",
    "university-of-pittsburgh": "upitt",
    "university-of-southern-california": "usc",
    "university-of-utah": "utah",
    "university-of-washington": "uw",
    "university-of-calgary": "ucalgary",
}

_STOP = frozenset(
    {"a", "at", "of", "and", "for", "the", "state", "school",
     "college", "institute", "university"}
)


def derive_abbrev(slug: str, displayed_abbrev: str | None = None) -> str:
    """Return a compact dataset prefix for a CCR university slug.

    Prefers the curated map, then a sanitised version of the abbreviation shown
    on CCR (e.g. "ASU", "U of T"), then a slug-derived fallback.
    """
    slug = (slug or "").strip().lower()
    if slug in KNOWN:
        return KNOWN[slug]
    if displayed_abbrev:
        token = re.sub("[^a-z0-9]+", "", displayed_abbrev.lower())
        if 2 <= len(token) <= 12:
            return token
    tokens = [t for t in re.split("[^a-z0-9]+", slug) if t and t not in _STOP]
    if not tokens:
        return re.sub("[^a-z0-9]+", "", slug) or "uni"
    if len(tokens) == 1:
        return tokens[0][:12]
    joined = "".join(tokens)
    return joined[:16]
