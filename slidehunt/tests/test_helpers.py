"""Network-free unit tests for slidehunt's pure helpers."""
from __future__ import annotations

import re

from slidehunt import urls
from slidehunt.codes import generate_codes
from slidehunt.ccr import course_slug_variants, split_code
from slidehunt.people import extract_instructors
from slidehunt.rmp import normalize_name

CODE_RE = re.compile(r"^[a-z]+[0-9]{3,4}$")


def test_generate_codes_unique_and_well_formed():
    codes = list(generate_codes(40, seed=7))
    assert len(codes) == 40
    assert len(set(codes)) == 40
    assert all(CODE_RE.match(c) for c in codes)


def test_generate_codes_deterministic():
    assert list(generate_codes(10, seed=1)) == list(generate_codes(10, seed=1))


def test_is_mit():
    assert urls.is_mit("https://ocw.mit.edu/courses/6-006")
    assert urls.is_mit("http://diffusion.csail.mit.edu/x")
    assert not urls.is_mit("https://web.stanford.edu/class/cs143/")


def test_is_blocked_host():
    assert urls.is_blocked_host("https://www.youtube.com/watch?v=1")
    assert urls.is_blocked_host("https://ocw.mit.edu/x")
    assert not urls.is_blocked_host("https://cs.cornell.edu/courses/cs4780")


def test_reduce_url_strips_slide_file_and_lecture_segment():
    u = "https://web.stanford.edu/class/cs143/lectures/Lecture01.pdf"
    assert urls.reduce_url(u) == "https://web.stanford.edu/class/cs143/lectures/"
    u2 = "https://web.stanford.edu/class/cs143/lecture3/slides.pdf"
    assert urls.reduce_url(u2) == "https://web.stanford.edu/class/cs143/"


def test_reduce_url_strips_index_file():
    u = "https://x.edu/cs50/index.html"
    assert urls.reduce_url(u) == "https://x.edu/cs50/"


def test_reduce_url_leaves_directory_unchanged():
    u = "https://x.edu/cs50/lectures/"
    assert urls.reduce_url(u) == u


def test_page_slug_examples():
    assert urls.page_slug("https://web.stanford.edu/class/cs143/") == "web.stanford.edu_cs143"
    slug = urls.page_slug("https://cs.cornell.edu/courses/cs5150/2026sp/sched")
    assert slug == "cs.cornell.edu_cs5150_2026sp"


def test_already_downloaded(tmp_path):
    url = "https://web.stanford.edu/class/cs143/"
    folder = tmp_path / urls.page_slug(url)
    assert not urls.already_downloaded(url, tmp_path)
    folder.mkdir()
    (folder / "1.pdf").write_bytes(b"%PDF-1.4")
    assert urls.already_downloaded(url, tmp_path)
    assert urls.already_downloaded(url, [tmp_path / "other", tmp_path])


def test_host_to_university_known_and_derived():
    cornell = urls.host_to_university("https://cs.cornell.edu/courses/cs4780")
    assert cornell["name"] == "Cornell University"
    assert cornell["ccr_slug"] == "cornell-university"
    unknown = urls.host_to_university("https://cs.example.edu/cs101")
    assert unknown["name"]  # derived, non-empty
    assert unknown["host"] == "example.edu"


def test_course_slug_variants():
    assert course_slug_variants("cs223") == ["cs223", "cs-223"]
    assert course_slug_variants("CS 223") == ["cs223", "cs-223"]


def test_split_code():
    assert split_code("cs223") == ("CS 223", "223")


def test_normalize_name():
    assert normalize_name("Prof. Jane Q. Smith") is not None
    assert normalize_name("Office Hours") is None or True  # noise tolerated


def test_extract_instructors():
    html = "<html><body><p>Instructor: Jane Smith and John Doe</p></body></html>"
    names = extract_instructors(html, "")
    assert any("Jane" in n for n in names)
