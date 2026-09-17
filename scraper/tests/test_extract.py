"""Tests for slide-link extraction and index-link discovery."""
from slidegrab.extract import discover_index_links, discover_slides, is_slide_link

BASE = "https://www.cse.iitm.ac.in/~teacher/cs7015/"

HTML = """
<html><body>
  <a href="lec01-intro.pdf">Lecture 1 slides</a>
  <a href="lecture-notes-02.pdf">Lecture 2 notes</a>
  <a href="hw1-solution.pdf">Homework 1 solution</a>
  <a href="week3.pptx">Week 3</a>
  <a href="readings.pdf">Readings</a>
  <a href="https://www.cse.iitm.ac.in/~teacher/cs7015/lectures.html">Lectures</a>
</body></html>
"""


def test_accepts_slide_pdf_with_token():
    ok, _ = is_slide_link("Lecture 1 slides", "lec01-intro.pdf")
    assert ok


def test_rejects_notes_and_solutions():
    assert not is_slide_link("Lecture 2 notes", "lecture-notes-02.pdf")[0]
    assert not is_slide_link("Homework 1 solution", "hw1-solution.pdf")[0]


def test_accepts_pptx_by_extension():
    ok, _ = is_slide_link("Week 3", "week3.pptx")
    assert ok


def test_rejects_plain_pdf_without_token():
    assert not is_slide_link("Readings", "readings.pdf")[0]


def test_discover_slides_filters_and_numbers():
    cands = discover_slides(HTML, BASE)
    urls = {c.url.rsplit("/", 1)[-1] for c in cands}
    assert "lec01-intro.pdf" in urls
    assert "week3.pptx" in urls
    assert "lecture-notes-02.pdf" not in urls
    assert "hw1-solution.pdf" not in urls
    lec1 = next(c for c in cands if c.url.endswith("lec01-intro.pdf"))
    assert lec1.lecture_number == 1


def test_discover_index_links_same_host_only():
    links = discover_index_links(HTML, BASE)
    assert any(l.endswith("lectures.html") for l in links)
