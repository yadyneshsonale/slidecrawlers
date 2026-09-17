"""Tests for course-code discovery."""
import asyncio

from config.settings import University
from slidegrab.coursecodes import discover_course_codes, extract_codes
from slidegrab.queries import build_queries
from slidegrab.search import SearchHit

UNI = University(
    name="Stanford University", slug="stanford", region="global", priority=200,
    aliases=("Stanford", "Stanford University"), domains=("stanford.edu", "cs.stanford.edu"),
)

TOPICS = {
    "topics": ["deep learning"],
    "slide_phrases": ["lecture slides"],
    "course_codes": {"stanford": ["CS229"]},
}


def test_extract_codes_handles_known_shapes():
    text = "CS229 and EE2703, MIT 6.036 / 6.S191, CMU 11-785 plus COL774 and CS60050"
    codes = extract_codes(text)
    assert {"CS229", "EE2703", "6.036", "6.S191", "11-785", "COL774", "CS60050"} <= codes


def test_extract_codes_rejects_years_and_blocklist():
    codes = extract_codes("Accepted at CVPR 2024 and ICML 2023, see FAQ 100")
    assert "CVPR2024" not in codes
    assert "ICML2023" not in codes
    assert not any(c.startswith("FAQ") for c in codes)


class _FakeSearch:
    def __init__(self, hits):
        self._hits = hits

    def search(self, query):  # noqa: D401 - mimic SearchClient.search
        return self._hits


def test_discover_returns_seed_and_found_codes():
    hits = [
        SearchHit("https://cs.stanford.edu/courses", "Courses",
                  "CS224N NLP and CS231N vision and CS229 ML"),
    ]
    codes = asyncio.run(
        discover_course_codes(UNI, _FakeSearch(hits), TOPICS, fetcher=None)
    )
    assert "CS229" in codes          # seed (weighted highest)
    assert "CS224N" in codes         # discovered from snippet
    assert "CS231N" in codes
    assert codes[0] == "CS229"       # seed ranks first


def test_discovered_codes_drive_queries():
    codes = ["CS224N", "CS231N"]
    qs = build_queries(UNI, TOPICS, codes=codes)
    code_qs = [q for q in qs if q.kind == "code"]
    assert {q.label for q in code_qs} == {"CS224N", "CS231N"}
    assert all("Stanford" in q.text for q in code_qs)
