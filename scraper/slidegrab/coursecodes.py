"""Step 1 of the pipeline: discover course codes for a university.

For each university we search for course-catalog / listing pages and extract
course codes (e.g. ``CS229``, ``EE2703``, ``6.036``, ``11-785``, ``COL774``).
Those codes then drive high-precision slide-deck queries in
``queries.build_queries`` (step 2), whose pages are downloaded in step 3.

Seed codes from ``topics.yaml`` are always included and trusted, but discovery
is not limited to them: any code matched on a catalog page (preferably on the
university's own domain) is added, ranked by how often it was seen.
"""
from __future__ import annotations

from urllib.parse import urlparse

import re

from bs4 import BeautifulSoup

from config.settings import University

# Course-code shapes observed across universities:
#   CS229, EE2703, COL774, ELL409, CS60050  -> letters + digits (+ optional trailing letter)
#   6.036, 6.S191                            -> MIT dotted
#   11-785, 15-281, 10-701                   -> CMU hyphenated
_LETTER_CODE = re.compile(r"\b([A-Z]{2,4})\s?-?\s?(\d{3,5}[A-Z]?)\b")
_DOTTED_CODE = re.compile(r"\b(\d{1,2}\.(?:S)?\d{2,3}[A-Z]?)\b")
_HYPHEN_CODE = re.compile(r"\b(\d{2}-\d{3})\b")

# Uppercase tokens that look like a code prefix but never are.
_PREFIX_BLOCKLIST = frozenset({
    "ISO", "ISBN", "IEEE", "ACM", "PDF", "PPT", "FAQ", "USA", "PHD", "GPA",
    "FALL", "HTTP", "HTML", "RSS", "API", "URL", "ROOM", "TEL", "FAX", "PIN",
    "ZIP", "GMT", "UTC", "CVPR", "ICML", "NIPS", "ECCV", "ICCV", "AAAI",
    "JAN", "FEB", "MAR", "APR", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
})

# Catalog-style query templates (filled with the university's primary alias).
_CATALOG_TEMPLATES = (
    "{uni} computer science course list",
    "{uni} CSE course catalog course codes",
    "{uni} courses offered electrical engineering",
)


def extract_codes(text: str) -> set[str]:
    """Pull normalized course codes out of arbitrary text."""
    codes: set[str] = set()
    for m in _LETTER_CODE.finditer(text):
        prefix, number = m.group(1).upper(), m.group(2).upper()
        if prefix in _PREFIX_BLOCKLIST:
            continue
        # Drop year-like 4-digit numbers (e.g. "CVPR 2024", "ICML 2023").
        if len(number) == 4 and number.isdigit() and 1900 <= int(number) <= 2099:
            continue
        codes.add(f"{prefix}{number}")
    for m in _DOTTED_CODE.finditer(text):
        codes.add(m.group(1).upper())
    for m in _HYPHEN_CODE.finditer(text):
        codes.add(m.group(1).upper())
    return codes


def _on_domain(url: str, domains: tuple[str, ...]) -> bool:
    host = urlparse(url).netloc.lower()
    return any(host == d or host.endswith("." + d) for d in domains)


def _visible_text(html: str, max_chars: int = 200_000) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text(" ", strip=True)[:max_chars]


async def discover_course_codes(
    uni: University,
    search,
    topics_cfg: dict,
    fetcher=None,
    *,
    max_queries: int = 3,
    max_pages: int = 2,
    max_codes: int = 24,
    seed: bool = True,
) -> list[str]:
    """Search for ``uni``'s catalog pages and return ranked course codes.

    Seed codes from ``topics.yaml`` (when ``seed``) are weighted highest, then
    codes are ranked by how often they appear across catalog hits/pages. When a
    ``fetcher`` is supplied, a couple of on-domain catalog pages are loaded for
    richer extraction; otherwise only search result titles/snippets are mined.
    """
    primary = uni.aliases[0] if uni.aliases else uni.name
    found: dict[str, int] = {}

    def _add(codes, weight: int = 1) -> None:
        for c in codes:
            found[c] = found.get(c, 0) + weight

    if seed:
        seeds = topics_cfg.get("course_codes", {}).get(uni.slug, [])
        _add({str(c).upper() for c in seeds}, weight=100)

    for tmpl in _CATALOG_TEMPLATES[:max_queries]:
        query = tmpl.format(uni=primary)
        try:
            hits = search.search(query)
        except Exception:  # noqa: BLE001 - one bad query shouldn't abort discovery
            continue
        for h in hits:
            _add(extract_codes(f"{h.title} {h.description}"))
        if fetcher is not None:
            on_domain = [h for h in hits if _on_domain(h.url, uni.domains)]
            for h in on_domain[:max_pages]:
                try:
                    res = await fetcher.fetch(h.url)
                except Exception:  # noqa: BLE001
                    continue
                if res is None:
                    continue
                _add(extract_codes(_visible_text(res.html)))

    ranked = sorted(found.items(), key=lambda kv: (-kv[1], kv[0]))
    return [code for code, _ in ranked[:max_codes]]
