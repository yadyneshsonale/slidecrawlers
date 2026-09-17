"""Tests for slide-link extraction and download filename logic."""
from pathlib import Path

from slidefetch.cli import _page_slug
from slidefetch.download import (
    _aia_ca_issuer_urls,
    _drive_confirmed_url,
    _filename,
    normalize_url,
    sanitize,
)
from slidefetch.drive import _clean_name, folder_id, is_drive_folder
from slidefetch.extract import find_slides, is_slide_link
from slidefetch.render import detect_framework, looks_like_slideshow

BASE = "https://www.cse.iitm.ac.in/~teacher/cs7015/"

HTML = """
<html><body>
  <a href="Slides/Lecture1.pdf">Lecture 1 slides</a>
  <a href="lecture-notes-02.pdf">Lecture 2 notes</a>
  <a href="hw1-solution.pdf">Homework 1 solution</a>
  <a href="week3.pptx">Week 3</a>
  <a href="readings.pdf">Readings</a>
  <a href="https://other.edu/intro.ppt">Intro deck</a>
</body></html>
"""


def test_accepts_slide_pdf_with_token():
    assert is_slide_link("Lecture 1 slides", "Slides/Lecture1.pdf")[0]


def test_rejects_notes_and_solutions():
    assert not is_slide_link("Lecture 2 notes", "lecture-notes-02.pdf")[0]
    assert not is_slide_link("Homework 1 solution", "hw1-solution.pdf")[0]


def test_accepts_ppt_and_pptx_by_extension():
    assert is_slide_link("Week 3", "week3.pptx")[0]
    assert is_slide_link("Intro deck", "https://other.edu/intro.ppt")[0]


def test_rejects_plain_pdf_without_token():
    assert not is_slide_link("Readings", "readings.pdf")[0]


def test_accepts_pdf_in_lecture_directory():
    # Bare "pdf" link text + no slide token in the filename, but the file lives
    # in a ``/Lec/`` directory (e.g. EECS 452): treat as a lecture deck.
    base = "https://eecs.umich.edu/courses/eecs452/Lec/"
    assert is_slide_link("pdf", base + "452L05F14_kurt.pdf")[0]
    assert is_slide_link("pdf", base + "HardWareOverview_MetzgerF14.pdf")[0]


def test_accepts_pdf_with_embedded_slides_keyword():
    # "Slides" embedded in a camelCase filename the word-boundary check misses.
    assert is_slide_link("pdf", "https://x.edu/refs/L01SlidesF14.pdf")[0]


def test_rejects_plain_pdf_outside_slide_dir():
    # A bare PDF not in a slide directory and without a slide keyword stays out.
    assert not is_slide_link("pdf", "https://x.edu/Docs/C5515_gpio_guide.pdf")[0]


def test_find_slides_filters_and_numbers():
    links = find_slides(HTML, BASE)
    names = {l.url.rsplit("/", 1)[-1] for l in links}
    assert "Lecture1.pdf" in names
    assert "week3.pptx" in names
    assert "intro.ppt" in names
    assert "lecture-notes-02.pdf" not in names
    assert "hw1-solution.pdf" not in names
    lec1 = next(l for l in links if l.url.endswith("Lecture1.pdf"))
    assert lec1.number == 1


def test_find_slides_sets_original_name():
    links = find_slides(HTML, BASE)
    lec1 = next(l for l in links if l.url.endswith("Lecture1.pdf"))
    assert lec1.name == "Lecture1.pdf"


def test_filename_prefixes_sequence_and_keeps_name():
    assert _filename(1, "EECS230_Lecture01.pdf", "pdf") == "1_EECS230_Lecture01.pdf"
    assert _filename(2, "1-Course Overview.pdf", "pdf") == "2_1-Course_Overview.pdf"


def test_filename_falls_back_to_sequence_only():
    assert _filename(5, None, "pdf") == "5.pdf"
    assert _filename(3, "", "pdf") == "3.pdf"


def test_is_drive_folder_detects_folder_urls():
    assert is_drive_folder("https://drive.google.com/drive/folders/1ABC_def-123")
    assert is_drive_folder("https://drive.google.com/drive/u/0/folders/1ABC")
    assert not is_drive_folder("https://drive.google.com/file/d/1ABC/view")
    assert not is_drive_folder("https://web.stanford.edu/class/cs143/")


def test_drive_folder_id():
    url = "https://drive.google.com/drive/folders/1IHIE0U81v_fvVMGbSqb"
    assert folder_id(url) == "1IHIE0U81v_fvVMGbSqb"


def test_drive_clean_name_strips_type_suffix():
    assert _clean_name("1-Course Overview.pdf PDF Shared") == "1-Course Overview.pdf"
    assert _clean_name("9-Latches & Flip-FlopsSR.pdf PDF Shared") == \
        "9-Latches & Flip-FlopsSR.pdf"
    assert _clean_name("Just A Folder") is None


def test_aia_ca_issuer_url_parsing():
    uri = b"http://crt.sectigo.com/InCommonRSAOVSSLCA3.crt"
    der = bytes.fromhex("2B06010505073002") + b"\x86" + bytes([len(uri)]) + uri
    assert _aia_ca_issuer_urls(der) == [uri.decode()]


def test_drive_confirmed_url_builds_usercontent_link():
    url = "https://drive.google.com/uc?export=download&id=ABC123"
    out = _drive_confirmed_url(url, b"<html>...&confirm=xyz&...</html>")
    assert out == (
        "https://drive.usercontent.google.com/download?"
        "id=ABC123&export=download&confirm=xyz"
    )


def test_drops_handout_variant_when_full_deck_present():
    html = """
    <html><head><title>Lecture Slides</title></head><body>
      <a href="pdf/01StableMatching.pdf">1. Stable Matching</a>
      <a href="pdf/01StableMatching-2x2.pdf">1. Stable Matching (2 per page)</a>
      <a href="pdf/02Graphs-4up.pdf">2. Graphs</a>
    </body></html>
    """
    names = {l.url.rsplit("/", 1)[-1] for l in find_slides(html, "http://x/")}
    assert "01StableMatching.pdf" in names
    assert "01StableMatching-2x2.pdf" not in names  # full deck wins
    assert "02Graphs-4up.pdf" in names  # no full version -> keep the handout


def test_sanitize_strips_unsafe_chars():
    assert sanitize("CS 7015: Intro/Deep!.pdf") == "CS_7015_Intro_Deep_.pdf"


def test_page_slug_avoids_generic_collisions():
    # Different courses whose pages both end in a generic segment must differ.
    a = _page_slug("https://web.stanford.edu/class/cs143/")
    b = _page_slug("https://www.cs.cornell.edu/courses/cs5150/2026sp/schedule.html")
    c = _page_slug("https://web.stanford.edu/class/cs110/lectures/")
    assert a == "web.stanford.edu_cs143"
    assert b == "cs.cornell.edu_cs5150_2026sp"
    assert c == "web.stanford.edu_cs110"
    assert len({a, b, c}) == 3


def test_normalize_url_rewrites_github_blob():
    out = normalize_url(
        "https://github.com/owner/repo/blob/master/Lecture%201.pdf"
    )
    assert out == "https://raw.githubusercontent.com/owner/repo/master/Lecture%201.pdf"


def test_normalize_url_encodes_spaces():
    out = normalize_url("https://x.edu/lec15-slides-testing 2.pdf")
    assert " " not in out
    assert out.endswith("lec15-slides-testing%202.pdf")


# --------------------------------------------------------------------------- #
# Client-side HTML slide decks (remark.js / reveal.js / impress.js)
# --------------------------------------------------------------------------- #

HTML_PRES = """
<html><body>
  <a href="presentations/ee361_intro.html">Introduction</a>
  <a href="presentations/ee361_ac_circuits.html">AC Power Calculations</a>
  <a href="index.html">Home</a>
  <a href="notes/lecture-notes.html">Notes</a>
  <a href="syllabus.pdf">Syllabus</a>
</body></html>
"""


def test_accepts_html_deck_in_presentations_dir():
    assert is_slide_link(
        "Introduction", "http://keysan.me/presentations/ee361_intro.html"
    )[0]


def test_accepts_html_deck_by_slide_keyword():
    assert is_slide_link("Lecture 5", "https://x.edu/m/lecture5-slides.html")[0]


def test_rejects_plain_html_pages():
    assert not is_slide_link("Home", "https://x.edu/index.html")[0]
    assert not is_slide_link("Schedule", "https://x.edu/schedule.html")[0]


def test_rejects_excluded_html_even_in_slide_dir():
    assert not is_slide_link(
        "Homework 1", "https://x.edu/presentations/hw1.html"
    )[0]


def test_find_slides_picks_html_decks_and_types_them():
    links = find_slides(HTML_PRES, "http://keysan.me/ee361/")
    by_name = {l.url.rsplit("/", 1)[-1]: l for l in links}
    assert "ee361_intro.html" in by_name
    assert "ee361_ac_circuits.html" in by_name
    assert by_name["ee361_intro.html"].file_type == "html"
    assert "index.html" not in by_name          # plain page rejected
    assert "lecture-notes.html" not in by_name   # notes rejected
    assert "syllabus.pdf" not in by_name         # syllabus rejected


def test_filename_strips_html_and_uses_pdf():
    assert _filename(1, "ee361_intro.html", "pdf") == "1_ee361_intro.pdf"
    assert _filename(2, "deck.htm", "pdf") == "2_deck.pdf"


def test_detect_framework_recognizes_known_decks():
    assert detect_framework("<script>var s = remark.create();</script>") == "remark"
    assert detect_framework('<div class="reveal"><div class="slides"></div></div>') == "reveal"
    assert detect_framework("<div id='impress'></div><script>impress().init()</script>") == "impress"


def test_detect_framework_rejects_plain_html():
    assert detect_framework("<html><body><h1>Course Home</h1></body></html>") is None
    assert not looks_like_slideshow("<html><body>nothing here</body></html>")
